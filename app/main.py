"""FastAPI app: routes, templates, background sync."""

import asyncio
import logging
import sqlite3
import time
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import checks, ipmath, models, store, views
from app.config import Settings, load_settings
from app.db import connect, init_db
from app.sync import get_state, is_paused, sync_loop, sync_once
from app.unifi_adapter import UnifiAdapter, UnifiError

HERE = Path(__file__).parent
log = logging.getLogger("ipam")


def _fmt_ts(value: int | str | None) -> str:
    if not value:
        return "never"
    return datetime.fromtimestamp(int(value)).strftime("%Y-%m-%d %H:%M")


def _ago(value: int | str | None) -> str:
    if not value:
        return "never"
    s = int(time.time()) - int(value)
    if s < 90:
        return "just now"
    if s < 5400:
        return f"{s // 60} min ago"
    if s < 172800:
        return f"{s // 3600} h ago"
    return f"{s // 86400} d ago"


def _prefix(vlan: models.Vlan) -> str:
    """'10.1.20.' for a /24 or smaller subnet, else '' (full addresses are typed)."""
    if vlan.net.prefixlen >= 24:
        return str(vlan.net.network_address).rsplit(".", 1)[0] + "."
    return ""


def _full_ip(vlan: models.Vlan, text: str) -> str:
    """Accept a bare host octet ('21') for /24 subnets, or a full address."""
    text = text.strip()
    prefix = _prefix(vlan)
    if prefix and "." not in text:
        return prefix + text
    return text


def _host_part(ip: object, vlan: models.Vlan) -> str:
    """What the range form shows: the last octet for /24, else the full address."""
    if ip is None:
        return ""
    return str(ip).rsplit(".", 1)[1] if _prefix(vlan) else str(ip)


def _short(ip: object, vlan: models.Vlan) -> str:
    """'.21' for an address inside the VLAN's /24, else the full address."""
    if ip is None:
        return ""
    if vlan.net.prefixlen >= 24:
        return "." + str(ip).rsplit(".", 1)[1]
    return str(ip)


def _safe_next(url: str, default: str) -> str:
    """Only same-site paths; never an absolute or protocol-relative URL."""
    return url if url.startswith("/") and not url.startswith("//") else default


def _with_params(url: str, **params: str) -> str:
    path, _, fragment = url.partition("#")
    sep = "&" if "?" in path else "?"
    return path + sep + urlencode(params) + ("#" + fragment if fragment else "")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    init_db(settings.db_path)
    adapter = UnifiAdapter(
        settings.unifi_url,
        settings.unifi_api_key,
        site=settings.unifi_site,
        verify_tls=settings.verify_tls,
    )

    # Visible pages check in (POST /presence) about once a minute. Without a check-in for
    # SYNC_IDLE_MINUTES the background sync pauses; the next check-in syncs right away.
    presence = {"seen": time.monotonic()}  # start awake, so the first sync runs

    def idle() -> bool:
        limit = settings.sync_idle_minutes * 60
        return limit > 0 and time.monotonic() - presence["seen"] > limit

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        stop = asyncio.Event()
        task = None
        if settings.sync_interval_seconds > 0 and settings.unifi_url:
            task = asyncio.create_task(
                sync_loop(settings.db_path, adapter, settings.sync_interval_seconds, stop, idle)
            )
        yield
        stop.set()
        if task:
            await task
        adapter.close()

    app = FastAPI(title="UniFi IPAM", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    @app.middleware("http")
    async def no_store(request: Request, call_next):
        """Pages reflect the database at request time; never serve them from a cache."""
        response = await call_next(request)
        if not request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["ts"] = _fmt_ts
    templates.env.filters["ago"] = _ago
    templates.env.filters["short"] = _short
    templates.env.filters["host_part"] = _host_part
    templates.env.filters["prefix"] = _prefix
    templates.env.filters["capfirst"] = lambda s: (s[:1].upper() + s[1:]) if s else s

    def db() -> Iterator[sqlite3.Connection]:
        conn = connect(settings.db_path)
        try:
            yield conn
        finally:
            conn.close()

    Db = Annotated[sqlite3.Connection, Depends(db)]

    def render(request: Request, name: str, conn: sqlite3.Connection, **ctx) -> HTMLResponse:
        ctx.setdefault("sync", get_state(conn))
        ctx.setdefault("check_count", checks.run_checks(conn, settings.stale_days).total)
        return templates.TemplateResponse(request, name, ctx)

    def render_vlan(
        request: Request,
        conn: sqlite3.Connection,
        vlan_id: int,
        *,
        edit: int | None = None,
        errors: list[str] | None = None,
        warnings: list[str] | None = None,
        saved: str | None = None,
        form: dict | None = None,
        zoom: str | None = None,
        forgot: str | None = None,
        err: str | None = None,
    ) -> HTMLResponse:
        view = views.vlan_detail(conn, vlan_id, zoom)
        if view is None:
            raise HTTPException(404, "VLAN not found")
        editing = store.get_range(conn, edit) if edit else None
        if editing and editing.vlan_id != vlan_id:
            editing = None
        return render(
            request,
            "vlan.html",
            conn,
            v=view,
            groups=views.device_groups(view),
            vlans=views.vlan_overview(conn),
            categories=store.categories(conn),
            editing=editing,
            errors=errors or [],
            no_renew=bool(errors),  # a re-rendered form post: reloading would resubmit it
            warnings=warnings or [],
            saved=saved,
            form=form or {},
            forgot=forgot,
            err=err,
        )

    # ------------------------------------------------------------ pages

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, conn: Db):
        vlans = store.list_vlans(conn)
        if not vlans:
            return render(request, "empty.html", conn)
        return RedirectResponse(f"/vlan/{vlans[0].id}", status_code=302)

    @app.get("/vlan/{vlan_id}", response_class=HTMLResponse)
    def vlan_page(
        request: Request,
        vlan_id: int,
        conn: Db,
        edit: int | None = None,
        saved: str | None = None,
        warn: str | None = None,
        zoom: str | None = None,
        forgot: str | None = None,
        err: str | None = None,
    ):
        return render_vlan(
            request,
            conn,
            vlan_id,
            edit=edit,
            saved=saved,
            warnings=[warn] if warn else None,
            zoom=zoom,
            forgot=forgot,
            err=err,
        )

    @app.get("/vlan/{vlan_id}/next-free")
    def next_free(vlan_id: int, range: str, conn: Db):
        try:
            ip = views.next_free(conn, vlan_id, range)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        return JSONResponse({"vlan_id": vlan_id, "range": range, "ip": str(ip) if ip else None})

    @app.post("/device/{mac}", response_class=HTMLResponse)
    def device_update(
        request: Request,
        mac: str,
        conn: Db,
        vlan_id: Annotated[int, Form()],
        category: Annotated[str, Form()] = "",
        name: Annotated[str | None, Form()] = None,
        note: Annotated[str | None, Form()] = None,
        fixed_ip: Annotated[str | None, Form()] = None,
        dns_name: Annotated[str | None, Form()] = None,
        zoom: Annotated[str, Form()] = "",
        unifi_fields: Annotated[str, Form()] = "",
    ):
        """Save one device row. Category is local; name, note, fixed IP and DNS name go to UniFi.

        Only fields that changed are sent. Nothing is written anywhere when a check fails.
        """
        if unifi_fields:  # the edit row showed these fields: empty means "clear it"
            name, note, fixed_ip = name or "", note or "", fixed_ip or ""
            dns_name = dns_name or ""
        d = store.get_device(conn, mac)
        vlan = store.get_vlan(conn, vlan_id)
        if d is None or vlan is None:
            raise HTTPException(404, "device or VLAN not found")
        if d.gear_model:
            return save_gear(request, conn, d, vlan_id, category, dns_name or "", zoom)
        errors: list[str] = []
        warnings: list[str] = []
        change: dict = {}
        if name is not None and name.strip() != (d.name or ""):
            change["name"] = name.strip()
        if note is not None and note.strip() != (d.unifi_note or ""):
            change["note"] = note.strip()
        if fixed_ip is not None:
            wanted = _full_ip(vlan, fixed_ip) if fixed_ip.strip() else ""
            current = str(d.fixed_ip) if d.fixed_ip else ""
            if wanted != current:
                if not wanted:
                    change["clear_fixed_ip"] = True
                else:
                    try:
                        ip = ipmath.parse_addr(wanted)
                    except ValueError:
                        errors.append(f"{fixed_ip.strip()} is not an IPv4 address")
                    else:
                        e, w = store.check_fixed_ip(conn, vlan, mac, ip)
                        errors += e
                        warnings += w
                        change["fixed_ip"] = str(ip)
                        change["network_id"] = vlan.unifi_id
        if dns_name is not None:
            wanted_dns = dns_name.strip().lower().rstrip(".")
            if wanted_dns != (d.dns_name or ""):
                if not wanted_dns:
                    change["clear_dns_name"] = True
                else:
                    errors += store.check_dns_name(conn, mac, wanted_dns)
                    change["dns_name"] = wanted_dns
            keeps_fixed = "fixed_ip" in change or (d.fixed_ip and "clear_fixed_ip" not in change)
            if wanted_dns and not keeps_fixed:
                errors.append(
                    "A DNS name needs a fixed IP, so the name keeps pointing at the device"
                )
            if "clear_fixed_ip" in change and d.dns_name and wanted_dns:
                errors.append("Remove the DNS name too, or keep the fixed IP")
        if change and not d.unifi_user_id:
            errors.append("UniFi's id for this device is not known yet; run a sync first")
        messages = [("bad", m) for m in errors]
        template = "partials/device_edit_row.html"
        if not errors:
            if change:
                try:
                    adapter.update_client(d.unifi_user_id, **change)
                except UnifiError as exc:
                    messages.append(("bad", f"UniFi refused: {exc}"))
                else:
                    try:
                        sync_once(settings.db_path, adapter)
                    except UnifiError as exc:
                        messages.append(("warn", f"Saved in UniFi, but the refresh failed: {exc}"))
            if not any(kind == "bad" for kind, _ in messages):
                store.update_device(conn, mac, category)
                template = "partials/device_row.html"
                if change:
                    text = "Saved in UniFi."
                    if "fixed_ip" in change or "clear_fixed_ip" in change:
                        text += (
                            " The address applies when the device renews its lease or reconnects."
                        )
                    messages.insert(0, ("ok", text))
                messages += [("warn", m) for m in warnings]
        return device_row(request, conn, mac, vlan_id, zoom or None, template, messages=messages)

    def save_gear(request, conn, d, vlan_id, category, dns_name, zoom) -> HTMLResponse:
        """UniFi gear: the DNS name is a record in UniFi's DNS records list; category is local."""
        wanted = dns_name.strip().lower().rstrip(".")
        address = d.fixed_ip or d.last_ip
        messages: list[tuple[str, str]] = []
        if wanted != (d.dns_name or ""):
            errors = store.check_dns_name(conn, d.mac, wanted) if wanted else []
            if wanted and address is None:
                errors.append("This device has no address for the name to point at")
            messages = [("bad", m) for m in errors]
            if not errors:
                try:
                    if not wanted:
                        adapter.delete_dns_record(d.dns_record_id)
                    elif d.dns_record_id:
                        adapter.update_dns_record(d.dns_record_id, wanted, str(address))
                    else:
                        adapter.create_dns_record(wanted, str(address))
                except UnifiError as exc:
                    messages.append(("bad", f"UniFi refused: {exc}"))
                else:
                    if wanted:
                        text = (
                            f"Saved as a custom DNS record in UniFi. {wanted} resolves to "
                            f"{address} within about 10 seconds."
                        )
                    else:
                        text = "Custom DNS record removed in UniFi."
                    messages.append(("ok", text))
                    try:
                        sync_once(settings.db_path, adapter)
                    except UnifiError as exc:
                        messages.append(("warn", f"Saved in UniFi, but the refresh failed: {exc}"))
        failed = any(kind == "bad" for kind, _ in messages)
        if not failed:
            store.update_device(conn, d.mac, category)
        template = "partials/device_edit_row.html" if failed else "partials/device_row.html"
        return device_row(request, conn, d.mac, vlan_id, zoom or None, template, messages=messages)

    def device_row(
        request: Request,
        conn: sqlite3.Connection,
        mac: str,
        vlan_id: int,
        zoom: str | None,
        template: str,
        messages: list[tuple[str, str]] | None = None,
    ) -> HTMLResponse:
        view = views.vlan_detail(conn, vlan_id, zoom)
        if view is None:
            raise HTTPException(404, "VLAN not found")
        row = next((r for r in view.devices if r.device.mac == mac.lower()), None)
        if row is None:
            raise HTTPException(404, "device not in this VLAN")
        return render(
            request,
            "partials/device_response.html",
            conn,
            row_template=template,
            messages=messages or [],
            r=row,
            v=view,
            categories=store.categories(conn),
            check_count=0,
        )

    @app.get("/device/{mac}/edit", response_class=HTMLResponse)
    def device_edit(request: Request, mac: str, conn: Db, vlan_id: int, zoom: str | None = None):
        return device_row(request, conn, mac, vlan_id, zoom, "partials/device_edit_row.html")

    @app.get("/device/{mac}/row", response_class=HTMLResponse)
    def device_view(request: Request, mac: str, conn: Db, vlan_id: int, zoom: str | None = None):
        return device_row(request, conn, mac, vlan_id, zoom, "partials/device_row.html")

    @app.post("/device/{mac}/forget")
    async def device_forget(mac: str, next: Annotated[str, Form()] = "/checks#stale"):
        """Forget the client in UniFi, then drop it locally. The one write to UniFi."""
        back = _safe_next(next, "/checks#stale")
        conn = connect(settings.db_path)
        try:
            device = store.get_device(conn, mac)
            if device is None:
                raise HTTPException(404, "device not found")
            if device.gear_model:
                raise HTTPException(400, "UniFi gear is not a client and cannot be forgotten here")
            try:
                await asyncio.to_thread(adapter.forget_clients, [mac])
            except UnifiError as exc:
                log.warning("forget %s failed: %s", mac, exc)
                return RedirectResponse(_with_params(back, err=str(exc)), status_code=303)
            store.delete_device(conn, mac)
        finally:
            conn.close()
        return RedirectResponse(_with_params(back, forgot=device.display_name), status_code=303)

    # ------------------------------------------------------------ ranges

    def _after_save(request: Request, conn: sqlite3.Connection, vlan_id: int, check, form, edit):
        if check.ok:
            q = {"saved": form["name"]}
            if check.warnings:
                q["warn"] = "; ".join(check.warnings)
            return RedirectResponse(f"/vlan/{vlan_id}?" + urlencode(q), status_code=303)
        return render_vlan(
            request,
            conn,
            vlan_id,
            edit=edit,
            errors=check.errors,
            warnings=check.warnings,
            form=form,
        )

    @app.post("/vlan/{vlan_id}/ranges", response_class=HTMLResponse)
    def range_create(
        request: Request,
        vlan_id: int,
        conn: Db,
        name: Annotated[str, Form()],
        start_ip: Annotated[str, Form()],
        end_ip: Annotated[str, Form()],
        category: Annotated[str, Form()] = "",
        note: Annotated[str, Form()] = "",
    ):
        vlan = store.get_vlan(conn, vlan_id)
        if vlan is None:
            raise HTTPException(404, "VLAN not found")
        form = dict(name=name, start_ip=start_ip, end_ip=end_ip, category=category, note=note)
        check = store.save_range(
            conn, vlan_id, name, _full_ip(vlan, start_ip), _full_ip(vlan, end_ip), category, note
        )
        return _after_save(request, conn, vlan_id, check, form, None)

    @app.post("/ranges/{range_id}", response_class=HTMLResponse)
    def range_update(
        request: Request,
        range_id: int,
        conn: Db,
        name: Annotated[str, Form()],
        start_ip: Annotated[str, Form()],
        end_ip: Annotated[str, Form()],
        category: Annotated[str, Form()] = "",
        note: Annotated[str, Form()] = "",
    ):
        existing = store.get_range(conn, range_id)
        if existing is None:
            raise HTTPException(404, "range not found")
        vlan = store.get_vlan(conn, existing.vlan_id)
        assert vlan is not None
        form = dict(name=name, start_ip=start_ip, end_ip=end_ip, category=category, note=note)
        check = store.save_range(
            conn,
            existing.vlan_id,
            name,
            _full_ip(vlan, start_ip),
            _full_ip(vlan, end_ip),
            category,
            note,
            range_id=range_id,
        )
        return _after_save(request, conn, existing.vlan_id, check, form, range_id)

    @app.post("/ranges/{range_id}/delete")
    def range_delete(range_id: int, conn: Db):
        existing = store.get_range(conn, range_id)
        if existing is None:
            raise HTTPException(404, "range not found")
        store.delete_range(conn, range_id)
        return RedirectResponse(f"/vlan/{existing.vlan_id}", status_code=303)

    # ------------------------------------------------------------ checks, sync, health

    @app.get("/checks", response_class=HTMLResponse)
    def checks_page(request: Request, conn: Db, err: str | None = None, forgot: str | None = None):
        report = checks.run_checks(conn, settings.stale_days)
        return render(
            request,
            "checks.html",
            conn,
            report=report,
            err=err,
            forgot=forgot,
            conflicts=report.conflicts,
            counts=report.counts(),
            stale_days=settings.stale_days,
            check_count=report.total,
        )

    @app.post("/checks/ignore")
    def check_ignore(
        conn: Db,
        kind: Annotated[str, Form()],
        subject: Annotated[str, Form()],
        ignore: Annotated[str, Form()] = "1",
        next: Annotated[str, Form()] = "/checks",
    ):
        if kind not in checks.KINDS:
            raise HTTPException(400, "unknown check kind")
        checks.set_ignored(conn, kind, subject, ignore == "1")
        return RedirectResponse(_safe_next(next, "/checks"), status_code=303)

    @app.post("/presence")
    async def presence_ping():
        """A visible page checks in. If the sync was paused, sync now and tell the page."""
        presence["seen"] = time.monotonic()
        if not await asyncio.to_thread(is_paused, settings.db_path):
            return JSONResponse({"resumed": False})
        try:
            await asyncio.to_thread(sync_once, settings.db_path, adapter)
        except UnifiError as exc:
            log.warning("sync on resume failed: %s", exc)
        return JSONResponse({"resumed": True})

    @app.post("/sync")
    async def sync_now(request: Request):
        try:
            await asyncio.to_thread(sync_once, settings.db_path, adapter)
        except UnifiError as exc:
            log.warning("manual sync failed: %s", exc)
        back = request.headers.get("referer") or "/"
        return RedirectResponse(back, status_code=303)

    @app.get("/health")
    def health(conn: Db):
        return JSONResponse({"ok": True, "sync": get_state(conn)})

    return app
