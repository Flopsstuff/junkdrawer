#!/usr/bin/env python3
"""
hu5710 — read and control a Philips HU5710/00 humidifying purifier over local CoAP.

    ./hu5710.py status                 human-readable state
    ./hu5710.py status --raw           every field, untouched
    ./hu5710.py list                   writable params and their allowed values
    ./hu5710.py get mode humidity
    ./hu5710.py set mode=sleep backlight=off
    ./hu5710.py watch 60               poll and print what changes
    ./hu5710.py tour 20                walk the ambient light scenes
    ./hu5710.py discover               find the device by MAC prefix

Field codes come from the philips-airpurifier-coap HA integration (kongo09).
No cloud, no app — straight CoAP to the device.
Needs: pip install aioairctrl   (discover also uses nmap + arp)
"""
import argparse, asyncio, json, re, subprocess, sys

from aioairctrl import CoAPClient

DEFAULT_HOST = "192.168.1.73"
MAC_PREFIX = "68:79:c4"           # the purifier's vendor OUI
RETRIES = 5                        # the session counter desyncs; just ask again


# ---------------------------------------------------------------- parameters

class P:
    """One writable parameter: a friendly name over a raw D-code."""

    def __init__(self, name, code, values=None, rng=None, unit="", doc=""):
        self.name, self.code, self.unit, self.doc = name, code, unit, doc
        self.values = values or {}          # label -> raw
        self.rng = rng                      # (min, max, step)

    def label(self, raw):
        for k, v in self.values.items():
            if v == raw:
                return k
        return str(raw)

    def parse(self, text):
        t = text.strip().lower()
        if t in self.values:
            return self.values[t]
        if re.fullmatch(r"-?\d+", t):
            n = int(t)
            if self.rng:
                lo, hi, step = self.rng
                if not (lo <= n <= hi):
                    raise ValueError(f"{self.name}: {n} outside {lo}..{hi}")
                if (n - lo) % step:
                    raise ValueError(f"{self.name}: must step by {step} from {lo}")
                return n
            if self.values and n not in self.values.values():
                raise ValueError(f"{self.name}: {n} not one of {sorted(self.values.values())}")
            return n
        raise ValueError(f"{self.name}: cannot read {text!r}; try {self.choices()}")

    def choices(self):
        if self.values:
            return "/".join(self.values)
        if self.rng:
            lo, hi, step = self.rng
            return f"{lo}..{hi} step {step}"
        return "<int>"


ONOFF = {"off": 0, "on": 1}

PARAMS = [
    P("power",      "D03102", ONOFF, doc="main power"),
    P("mode",       "D0310C", {"auto": 0, "sleep": 17, "medium": 19, "high": 65},
      doc="preset; fan speed follows"),
    P("backlight",  "D03105", {"off": 0, "medium": 115, "on": 123}, doc="display brightness"),
    P("lamp",       "D03135", {"off": 0, "humidity": 1, "ambient": 2}, doc="what the lamp shows"),
    P("ambient",    "D03137", {"warm": 1, "dawn": 2, "calm": 3, "breath": 4},
      doc="ambient scene; needs lamp=ambient"),
    P("humidity-target", "D03128", rng=(30, 70, 5), unit="%", doc="target RH"),
    P("timer",      "D03110",
      {"off": 0, **{f"{h}h": h + 1 for h in range(1, 13)}}, doc="auto-off timer"),
    P("beep",       "D03130", ONOFF, doc="button sounds"),
    P("childlock",  "D03103", ONOFF, doc="lock the panel"),
    P("standby-sensors", "D03134", ONOFF, doc="keep sensors alive in standby"),
    P("auto-quickdry",   "D03138", ONOFF, doc="dry the wick automatically"),
    P("quickdry",        "D03139", ONOFF, doc="dry the wick now"),
]
BY_NAME = {p.name: p for p in PARAMS}
BY_CODE = {p.code: p for p in PARAMS}

# read-only fields: code -> (label, formatter)
SENSORS = [
    ("D0310D", "fan speed",      str),          # follows `mode`, not set directly
    ("D03125", "humidity",       lambda v: f"{v} %"),
    ("D03224", "temperature",    lambda v: f"{v/10:.1f} C"),
    ("D0312B", "humidifying",    str),
    ("D03240", "error code",     lambda v: f"{v}" + ("  (no faults)" if v == 0 else "  <-- CHECK")),
]
INFO = [
    ("D01S03", "name"), ("D01S05", "model"), ("D01S04", "type"),
    ("D01S12", "firmware"), ("WifiVersion", "wifi stack"),
    ("ConnectType", "cloud"), ("rssi", "rssi"),
]


# ---------------------------------------------------------------- transport

async def read(client):
    last = None
    for i in range(RETRIES):
        try:
            s = await client.get_status()
            return s[0] if isinstance(s, tuple) else s
        except Exception as e:                      # noqa: BLE001 - any decode hiccup
            last = e
            await asyncio.sleep(1.2)
    raise RuntimeError(f"could not read status after {RETRIES} tries: {last}")


async def connect(host):
    return await CoAPClient.create(host)


def discover():
    """Find the purifier in the ARP table by its vendor prefix."""
    subprocess.run(["nmap", "-sn", "-T4", "192.168.1.0/24"],
                   capture_output=True, timeout=120)
    out = subprocess.run(["arp", "-an"], capture_output=True, text=True).stdout
    hits = []
    for line in out.splitlines():
        if MAC_PREFIX in line.lower():
            m = re.search(r"\((\d+\.\d+\.\d+\.\d+)\)", line)
            if m:
                hits.append((m.group(1), line.strip()))
    return hits


# ---------------------------------------------------------------- rendering

def pct(part, total):
    return f"{100*part/total:.0f}%" if total else "?"


def show(s):
    get = s.get
    print(f"=== {get('D01S03')} — {get('D01S05')} ({get('D01S04')}) ===")
    for code, lab in INFO[3:]:
        print(f"  {lab:14s} {get(code)}")
    rt = get("Runtime", 0)
    print(f"  {'uptime':14s} ~{rt/60000:.1f} min")
    print(f"  {'free memory':14s} {get('free_memory')}")

    print("\n  -- state --")
    for p in PARAMS:
        raw = get(p.code)
        if raw is None:
            continue
        lab = p.label(raw)
        extra = f"{p.unit}" if p.unit else ""
        shown = f"{lab}{extra}" if lab.isdigit() else lab
        print(f"  {p.name:16s} {shown:12s} (raw {raw})")

    print("\n  -- sensors --")
    for code, lab, fmt in SENSORS:
        raw = get(code)
        if raw is not None:
            print(f"  {lab:16s} {fmt(raw)}")
    # D03211 holds its last value after the timer is cleared, so it only means
    # something while D03110 is non-zero.
    if get("D03110"):
        print(f"  {'timer remaining':16s} {get('D03211')} min")

    print("\n  -- filters --")
    pre, pre_t = get("D0520D", 0), get("D05207", 0)
    nano, nano_t = get("D0540E", 0), get("D05408", 0)
    print(f"  {'pre-filter':16s} {pre}/{pre_t} h   {pct(pre, pre_t)}")
    print(f"  {'NanoProtect':16s} {nano}/{nano_t} h   {pct(nano, nano_t)}")

    known = ({p.code for p in PARAMS} | {c for c, _, _ in SENSORS}
             | {c for c, _ in INFO}
             | {"D0520D", "D05207", "D0540E", "D05408", "Runtime", "free_memory",
                "ProductId", "DeviceId", "StatusType",
                "D03104", "D03211"})   # mirrors D03105 (backlight); observed, not documented
    rest = {k: v for k, v in s.items() if k not in known}
    if rest:
        print(f"\n  -- unmapped --\n  {rest}")


def show_list():
    print("writable parameters:\n")
    for p in PARAMS:
        print(f"  {p.name:18s} {p.code:8s} {p.choices():34s} {p.doc}")
    print("\nread-only: " + ", ".join(l for _, l, _ in SENSORS)
          + ", filters, rssi, uptime")
    print("\nexamples:\n  ./hu5710.py set mode=sleep backlight=off"
          "\n  ./hu5710.py set humidity-target=55\n  ./hu5710.py set lamp=ambient ambient=calm")


# ---------------------------------------------------------------- commands

async def cmd_status(a):
    c = await connect(a.host)
    try:
        s = await read(c)
    finally:
        await c.shutdown()
    print(json.dumps(s, indent=2, sort_keys=True)) if a.raw else show(s)


async def cmd_get(a):
    c = await connect(a.host)
    try:
        s = await read(c)
    finally:
        await c.shutdown()
    for want in a.names:
        p = BY_NAME.get(want)
        if p:
            raw = s.get(p.code)
            print(f"{want} = {p.label(raw)}  (raw {raw}, {p.code})")
        elif want in s:
            print(f"{want} = {s[want]}")
        else:
            hit = next((c_ for c_ in s if c_.lower() == want.lower()), None)
            print(f"{want} = {s[hit]}" if hit else f"{want}: unknown")


async def cmd_set(a):
    plan = []
    for pair in a.pairs:
        if "=" not in pair:
            sys.exit(f"expected name=value, got {pair!r}")
        name, _, val = pair.partition("=")
        name = name.strip()
        p = BY_NAME.get(name) or BY_CODE.get(name.upper())
        if not p:
            sys.exit(f"unknown parameter {name!r}; run: ./hu5710.py list")
        try:
            plan.append((p, p.parse(val)))
        except ValueError as e:
            sys.exit(str(e))

    c = await connect(a.host)
    try:
        before = await read(c)
        for p, raw in plan:
            was = before.get(p.code)
            print(f"  {p.name}: {p.label(was)} -> {p.label(raw)}  ({p.code}={raw})")
            await c.set_control_value(p.code, raw)
            await asyncio.sleep(1.0)
        await asyncio.sleep(1.5)
        after = await read(c)
    finally:
        await c.shutdown()

    print("\nverifying:")
    bad = 0
    for p, raw in plan:
        got = after.get(p.code)
        ok = got == raw
        bad += not ok
        print(f"  {p.name:18s} {'ok' if ok else f'MISMATCH (device says {got})'}")
    sys.exit(1 if bad else 0)


async def cmd_watch(a):
    c = await connect(a.host)
    try:
        prev = await read(c)
        print(f"watching {a.host} for {a.seconds:.0f}s — printing only changes\n", flush=True)
        waited = 0.0
        while waited < a.seconds:
            await asyncio.sleep(a.interval)
            waited += a.interval
            cur = await read(c)
            for k, v in cur.items():
                if k in ("Runtime", "free_memory") or prev.get(k) == v:
                    continue
                p = BY_CODE.get(k)
                if p:
                    print(f"[{waited:5.0f}s] {p.name:16s} {p.label(prev.get(k))} -> {p.label(v)}", flush=True)
                else:
                    print(f"[{waited:5.0f}s] {k:16s} {prev.get(k)} -> {v}", flush=True)
            prev = cur
    finally:
        await c.shutdown()
    print("\ndone")


async def cmd_tour(a):
    lamp, amb = BY_NAME["lamp"], BY_NAME["ambient"]
    c = await connect(a.host)
    try:
        before = await read(c)
        orig = {p.code: before.get(p.code) for p in (lamp, amb)}
        print(f"original lamp={lamp.label(orig[lamp.code])} "
              f"ambient={amb.label(orig[amb.code])}\n", flush=True)
        for label, raw in amb.values.items():
            await c.set_control_value(lamp.code, 0)       # blink, so the change reads
            await asyncio.sleep(1.5)
            await c.set_control_value(lamp.code, 2)
            await asyncio.sleep(0.5)
            print(f"  scene {raw} '{label}' — {a.dwell:.0f}s", flush=True)
            await c.set_control_value(amb.code, raw)
            await asyncio.sleep(a.dwell)
        print("\nrestoring", flush=True)
        for code, raw in orig.items():
            if raw is not None:
                await c.set_control_value(code, raw)
                await asyncio.sleep(1.0)
    finally:
        await c.shutdown()


def cmd_discover(a):
    hits = discover()
    if not hits:
        print(f"nothing with MAC prefix {MAC_PREFIX} on the LAN")
        return
    for ip, line in hits:
        print(f"{ip}   {line}")


# ---------------------------------------------------------------- entry

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=DEFAULT_HOST)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status", help="show current state")
    s.add_argument("--raw", action="store_true")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("list", help="show writable parameters")
    s.set_defaults(fn=None)

    s = sub.add_parser("get", help="read named fields")
    s.add_argument("names", nargs="+")
    s.set_defaults(fn=cmd_get)

    s = sub.add_parser("set", help="write name=value pairs")
    s.add_argument("pairs", nargs="+")
    s.set_defaults(fn=cmd_set)

    s = sub.add_parser("watch", help="print changes as they happen")
    s.add_argument("seconds", nargs="?", type=float, default=60)
    s.add_argument("--interval", type=float, default=5)
    s.set_defaults(fn=cmd_watch)

    s = sub.add_parser("tour", help="walk the ambient light scenes")
    s.add_argument("dwell", nargs="?", type=float, default=15)
    s.set_defaults(fn=cmd_tour)

    s = sub.add_parser("discover", help="find the device on the LAN")
    s.set_defaults(fn="sync-discover")

    a = ap.parse_args()
    if a.cmd == "list":
        show_list()
    elif a.fn == "sync-discover":
        cmd_discover(a)
    else:
        asyncio.run(a.fn(a))


if __name__ == "__main__":
    main()
