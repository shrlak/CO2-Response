"""Building the page from Python (runs in the browser under Pyodide).

The page is made of HTML strings. Buttons and inputs name a Python action in a
``data-act`` (click), ``data-change`` (inputs: fires on Enter or when the field is
left) or ``data-input`` (fires continuously, e.g. while a slider is dragged)
attribute; one listener on the document dispatches every event to the
registered Python function. Re-rendering a section therefore never leaks listeners.
"""

from __future__ import annotations

import asyncio
import inspect
import traceback
from html import escape
from typing import Callable

import js
from pyodide.ffi import create_proxy, to_js

try:  # JavaScript null arrives as this sentinel (not None) in current Pyodide
    from pyodide.ffi import jsnull
except ImportError:  # older Pyodide converts null to None
    jsnull = None

document = js.document
_actions: dict[str, Callable] = {}
_keep: list = []
_html: dict[str, str] = {}
_toast_timer = [None]
#: where the click being handled landed (viewport pixels), for actions on a canvas
last_click = [0.0, 0.0]


def nn(v):
    """None for JavaScript null/undefined, else the value."""
    return None if v is None or v is jsnull else v


def esc(s) -> str:
    return escape("" if s is None else str(s), quote=True)


def obj(d: dict):
    """Python dict → plain JavaScript object."""
    return to_js(d, dict_converter=js.Object.fromEntries)


def proxy(fn: Callable):
    """A JavaScript callable for ``fn`` that lives as long as the page."""
    p = create_proxy(fn)
    _keep.append(p)
    return p


def data(el, key: str) -> str | None:
    """An element's ``data-<key>`` attribute, or None (reading a missing JS property raises in Pyodide)."""
    v = el.getAttribute(f"data-{key}")
    return v if isinstance(v, str) else None


def action(name: str, fn: Callable) -> None:
    _actions[name] = fn


def actions(**fns: Callable) -> None:
    _actions.update(fns)


def run(coro) -> None:
    """Run a coroutine in the background, reporting errors instead of losing them."""

    async def guard():
        try:
            await coro
        except Exception as e:  # noqa: BLE001
            report(e)

    asyncio.ensure_future(guard())


def report(e: BaseException) -> None:
    js.console.error("".join(traceback.format_exception(e)))
    toast(f"Error: {e}", 8000)


def _dispatch(name: str | None, el) -> None:
    fn = _actions.get(name) if name else None
    if fn is None:
        return
    try:
        r = fn(el)
        if inspect.iscoroutine(r):
            run(r)
    except Exception as e:  # noqa: BLE001
        report(e)


def install() -> None:
    def on_click(ev):
        el = nn(ev.target.closest("[data-act]"))
        if el is not None and not el.hasAttribute("disabled"):
            last_click[0], last_click[1] = float(ev.clientX), float(ev.clientY)
            _dispatch(data(el, "act"), el)

    def on_change(ev):
        el = ev.target
        if data(el, "change"):
            _dispatch(data(el, "change"), el)

    def on_input(ev):
        el = ev.target
        if data(el, "input"):
            _dispatch(data(el, "input"), el)

    document.addEventListener("click", proxy(on_click))
    document.addEventListener("change", proxy(on_change))
    document.addEventListener("input", proxy(on_input))


def byid(i: str):
    return nn(document.getElementById(i))


def set_html(i: str, html: str, force: bool = False) -> bool:
    """Replace an element's content, only if it changed. Returns True if it did."""
    if not force and _html.get(i) == html:
        return False
    el = byid(i)
    if el is None:
        return False
    el.innerHTML = html
    _html[i] = html
    return True


def forget(prefix: str = "") -> None:
    """Forget cached content (after the elements were replaced)."""
    for k in [k for k in _html if k.startswith(prefix)]:
        del _html[k]


def set_text(i: str, text: str) -> None:
    el = byid(i)
    if el is not None and el.textContent != text:
        el.textContent = text


def editing_inside(i: str) -> bool:
    """True while the user is typing in a field inside element ``i`` (a switch or a
    drop-down that keeps focus after it was used does not count)."""
    a = nn(document.activeElement)
    el = byid(i)
    if a is None or el is None or not el.contains(a):
        return False
    return a.tagName == "TEXTAREA" or (a.tagName == "INPUT" and str(a.type) not in ("checkbox", "radio", "button", "range", "file"))


def toast(msg: str, ms: int = 3500) -> None:
    el = byid("toast")
    if el is None:
        return
    el.textContent = msg
    el.hidden = False
    if _toast_timer[0] is not None:
        js.clearTimeout(_toast_timer[0])
    _toast_timer[0] = js.setTimeout(proxy_once(lambda: setattr(byid("toast"), "hidden", True)), ms)


def proxy_once(fn: Callable):
    """A JavaScript callable that frees itself after its first call."""
    holder = []

    def call(*a):
        try:
            return fn()
        finally:
            holder[0].destroy()

    p = create_proxy(call)
    holder.append(p)
    return p


def after(ms: float, fn: Callable) -> None:
    js.setTimeout(proxy_once(fn), ms)


def download(name: str, parts, mime: str = "text/csv") -> None:
    """Save text or bytes (one piece or a list of pieces) as a file in the browser's Downloads."""
    if isinstance(parts, (str, bytes)):
        parts = [parts]
    blob = js.Blob.new(to_js(list(parts)), obj({"type": mime}))
    url = js.URL.createObjectURL(blob)
    a = document.createElement("a")
    a.href = url
    a.download = name
    document.body.append(a)
    a.click()
    a.remove()
    after(60000, lambda: js.URL.revokeObjectURL(url))


async def read_files(input_el) -> list[tuple[str, str]]:
    """(name, text) for each file chosen in an <input type=file>, then clears it."""
    out = []
    files = input_el.files
    for i in range(files.length):
        f = files.item(i)
        out.append((f.name, await f.text()))
    input_el.value = ""
    return out


def css_var(name: str) -> str:
    return js.getComputedStyle(document.documentElement).getPropertyValue(name).strip()


# ------------------------------------------------------------------ small HTML builders


def attrs(**kw) -> str:
    out = []
    for k, v in kw.items():
        if v is None or v is False:
            continue
        k = k.rstrip("_").replace("_", "-")
        out.append(f" {k}" if v is True else f' {k}="{esc(v)}"')
    return "".join(out)


def btn(text: str, act: str, cls: str = "btn", arg=None, disabled: bool = False, title: str | None = None, **kw) -> str:
    return f"<button type=\"button\" class=\"{cls}\"{attrs(data_act=act, data_arg=arg, disabled=disabled, title=title, **kw)}>{text}</button>"


def field(label: str, control: str, hint: str | None = None, for_: str | None = None) -> str:
    lab = f'<label for="{esc(for_)}">{label}</label>' if for_ else f'<span class="flabel">{label}</span>'
    h = f'<div class="hint">{hint}</div>' if hint else ""
    return f'<div class="field">{lab}<div class="ctl">{control}</div>{h}</div>'


def num(change: str, value, id_: str | None = None, step=None, lo=None, hi=None, arg=None, aria: str | None = None, width: int | None = None) -> str:
    v = "" if value is None or value != value else f"{value:.8g}"
    style = f"width:{width}px" if width else None
    return f'<input type="number"{attrs(id=id_, value=v, step=step if step is not None else "any", min=lo, max=hi, data_change=change, data_arg=arg, aria_label=aria, style=style)}>'


def number_field(label: str, change: str, value, unit: str | None = None, hint: str | None = None, id_: str | None = None, lo=None, hi=None, step=None, arg=None) -> str:
    u = f'<span class="u">{esc(unit)}</span>' if unit is not None else ""
    return field(label, num(change, value, id_, step, lo, hi, arg) + u, hint, id_)


def seg(act: str, value, options, arg_prefix: str = "") -> str:
    b = "".join(
        f'<button type="button"{attrs(data_act=act, data_arg=f"{arg_prefix}{v}", aria_pressed="true" if v == value else "false")}>{esc(t)}</button>' for v, t in options
    )
    return f'<div class="seg" role="group">{b}</div>'


def toggle(change: str, checked: bool, id_: str, arg=None) -> str:
    return f'<input type="checkbox"{attrs(id=id_, checked=checked, data_change=change, data_arg=arg)}>'


def select(change: str, value, options, id_: str | None = None, cls: str = "wide", arg=None) -> str:
    o = "".join(f'<option value="{esc(v)}"{" selected" if str(v) == str(value) else ""}>{esc(t)}</option>' for v, t in options)
    return f'<select{attrs(id=id_, class_=cls, data_change=change, data_arg=arg)}>{o}</select>'


def stat(k: str, v: str, live: str | None = None) -> str:
    return f'<div><div class="k">{esc(k)}</div><div class="v"{attrs(id=live)}>{esc(v)}</div></div>'


def as_float(el) -> float | None:
    try:
        v = float(el.value)
    except (TypeError, ValueError):
        return None
    return v if v == v else None
