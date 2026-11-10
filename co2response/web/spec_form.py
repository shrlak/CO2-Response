"""A settings form built from a field list in ``co2response/forms.py`` (browser).

The desktop window builds the same forms from the same lists (``ui/spec_form.py``).
``spec_form_html`` gives the rows; ``install_spec_actions`` registers the handlers
that turn an edit into a settings change. Inputs are named ``<prefix>-<field name>``.
"""

from __future__ import annotations

from typing import Any, Callable, Sequence

from ..forms import Field, patch_for
from .dom import actions, as_float, data, esc, field, num, seg, toggle


def spec_form_html(fields: Sequence[Field], obj: Any, prefix: str, action: str, snapshot: Any = None) -> str:
    """The rows of a form. ``action`` names the handlers (see ``install_spec_actions``);
    ``prefix`` keeps the ids apart when the same form is on the page twice."""
    out = []
    for f in fields:
        if not f.shown(obj):
            continue
        cur = getattr(obj, f.key)
        i = f"{prefix}-{f.name}"
        hint = esc(f.hint_for(obj) + (f.live(snapshot) if f.live and snapshot is not None else "")) or None
        unit = f'<span class="u">{esc(f.unit)}</span>' if f.unit else ""
        if f.kind == "toggle":
            out.append(field(esc(f.label), toggle(f"{action}-bool", bool(cur), i, f.key), hint, i))
        elif f.kind == "choice":
            out.append(field(esc(f.label), seg(f"{action}-choice", cur, f.options, arg_prefix=f"{f.key}:"), hint))
        elif f.kind == "optional":
            out.append(field(esc(f.label), toggle(f"{action}-on", cur is not None, f"{i}on", f.key), hint, f"{i}on"))
            if cur is not None:
                out.append(field(esc(f.value_label or f.label), num(action, f.to_display(cur), i, f.step, f.lo, f.hi, f.key) + unit, None, i))
        else:
            out.append(field(esc(f.label), num(action, f.to_display(cur), i, f.step, f.lo, f.hi, f.key) + unit, hint, i))
    return "".join(out)


def install_spec_actions(action: str, fields: Sequence[Field], get: Callable[[], Any], apply: Callable[[dict], None], done: Callable[[], None]) -> None:
    """Register ``action`` (numbers), ``action-bool`` (switches), ``action-on`` (the
    switch of an optional number) and ``action-choice``. ``apply(patch)`` saves a
    change; ``done()`` redraws."""
    by_key = {f.key: f for f in fields}

    def edit(key: str | None, **change) -> None:
        f = by_key.get(key or "")
        if f is not None:
            patch = patch_for(f, get(), **change)
            if patch:
                apply(patch)
        done()

    def on_number(el) -> None:
        v = as_float(el)
        f = by_key.get(data(el, "arg") or "")
        if v is None and (f is None or f.kind != "blank" or str(el.value).strip()):
            done()  # not a number: put the saved value back
            return
        edit(data(el, "arg"), shown=v)

    def on_choice(el) -> None:
        key, _, value = (data(el, "arg") or "").partition(":")
        edit(key, choice=value)

    actions(
        **{
            action: on_number,
            f"{action}-bool": lambda el: edit(data(el, "arg"), on=bool(el.checked)),
            f"{action}-on": lambda el: edit(data(el, "arg"), on=bool(el.checked)),
            f"{action}-choice": on_choice,
        }
    )
