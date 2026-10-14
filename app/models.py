"""The data shapes the app works with. Plain dataclasses, no SQL and no UniFi."""

from __future__ import annotations

from dataclasses import dataclass, field
from ipaddress import IPv4Network

from app import ipmath, oui
from app.ipmath import Addr, Utilisation

GROUP = 32  # strip cells are grouped on address boundaries that are multiples of 32


@dataclass(frozen=True)
class Vlan:
    id: int
    unifi_id: str
    name: str
    vlan_tag: int | None
    net: IPv4Network
    gateway: Addr
    dhcp: tuple[Addr, Addr] | None

    @property
    def subnet(self) -> str:
        return str(self.net)


@dataclass(frozen=True)
class IpRange:
    id: int
    vlan_id: int
    name: str
    start: Addr
    end: Addr
    category: str
    note: str

    @property
    def size(self) -> int:
        return ipmath.range_size(self.start, self.end)


@dataclass(frozen=True)
class Device:
    mac: str
    name: str | None
    hostname: str | None
    vendor: str | None
    category: str
    first_seen: int | None
    last_seen: int | None
    last_ip: Addr | None
    fixed_ip: Addr | None
    unifi_note: str | None
    vlan_unifi_id: str | None
    online: bool
    unifi_user_id: str | None = None
    dns_name: str | None = None  # UniFi Local DNS Record
    gear_model: str | None = None  # UniFi's own gear: its type and model, e.g. "Switch · US48PRO"
    dns_record_id: str | None = None  # gear only: its record in UniFi's DNS records list

    @property
    def display_name(self) -> str:
        return self.name or self.hostname or "(unnamed)"

    @property
    def private_mac(self) -> bool:
        """Randomised Wi-Fi address: no manufacturer can be known."""
        return oui.is_private(self.mac)

    @property
    def unknown(self) -> bool:
        return not self.name and not self.category

    @property
    def address(self) -> Addr | None:
        """Where the device is, or will be: live address when online, else its reservation,
        else the last lease. An offline device's old lease does not outrank its reservation."""
        if self.online and self.last_ip is not None:
            return self.last_ip
        return self.fixed_ip or self.last_ip

    @property
    def claims(self) -> set[Addr]:
        """Addresses this device holds right now: the live one when online, plus a reservation.
        An offline device's old lease holds nothing; DHCP hands it out again."""
        out = {self.fixed_ip} if self.fixed_ip is not None else set()
        if self.online and self.last_ip is not None:
            out.add(self.last_ip)
        return out

    @property
    def ips(self) -> set[Addr]:
        return {a for a in (self.address, self.fixed_ip) if a is not None}


@dataclass(frozen=True)
class Cell:
    """One host address in the per-address strip."""

    ip: Addr
    kind: str  # gateway | bad | used-reserved | used | reserved | dhcp | free
    title: str
    hue: int | None = None  # colour slot of the range this address belongs to

    @property
    def group_start(self) -> bool:
        """Strip separators sit before .0, .32, .64 ... (multiples of 32), like port blocks."""
        return int(self.ip) % GROUP == 0


@dataclass(frozen=True)
class Mark:
    """Label under the strip for one range."""

    start: Addr
    name: str
    hue: int | None  # None = the DHCP pool (from UniFi, not a planned range)
    key: str = ""  # zoom key: range id, or "dhcp"
    style: str = ""  # exact left/width over the strip, separators included


@dataclass(frozen=True)
class RangeStats:
    """Taken and free addresses in a range, counted like the strip: devices plus gateway."""

    used: int
    free: int
    next_free: Addr | None


@dataclass(frozen=True)
class Zoom:
    """The strip zoomed to one range or the DHCP pool."""

    key: str
    name: str
    start: Addr
    end: Addr
    hue: int | None
    size: int
    used: int
    next_free: Addr | None
    window_style: str = ""  # outline position on the minimap


@dataclass(frozen=True)
class DeviceGroup:
    """A run of devices in the devices table that sit in the same range.

    kind is "range" (a planned range), "dhcp" (the UniFi DHCP pool, outside any planned
    range) or "none". A planned range overlapping the pool wins, as on the strip.
    """

    kind: str
    range: IpRange | None
    hue: int | None
    rows: list[DeviceRow]
    stats: RangeStats | None
    start: Addr | None = None
    end: Addr | None = None


@dataclass
class VlanView:
    vlan: Vlan
    util: Utilisation
    ranges: list[IpRange]
    devices: list[DeviceRow] = field(default_factory=list)
    cells: list[Cell] = field(default_factory=list)
    marks: list[Mark] = field(default_factory=list)
    hues: dict[int, int] = field(default_factory=dict)  # range id -> colour slot
    bad_ips: set[Addr] = field(default_factory=set)
    dhcp_size: int = 0
    zoom: Zoom | None = None
    stats: dict[int, RangeStats] = field(default_factory=dict)  # range id -> stats
    dhcp_stats: RangeStats | None = None
    zoom_cells: list[Cell] = field(default_factory=list)
    shown: list[DeviceRow] = field(default_factory=list)  # devices table (filtered by zoom)


@dataclass(frozen=True)
class DeviceRow:
    device: Device
    ip: Addr | None  # the address relevant to this VLAN
    range_name: str | None
    inherited: str | None = None  # category taken over from the range, when none is set

    @property
    def category(self) -> str:
        """Effective category: the device's own, else the one of the range it sits in."""
        return self.device.category or self.inherited or ""

    @property
    def unknown(self) -> bool:
        return not self.device.name and not self.category
