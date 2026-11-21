"""The basket's QR target: `/r/{code}` offer page and its redeem call (architecture §6.2).

Phones reach it through the tunnel (ADR 0008 D3). Thin transport: validate → call the
demo controls in a worker thread → map domain errors. The page is self-contained HTML
(no app bundle) and stores only a random device nonce in localStorage, never a person.
"""

from __future__ import annotations

import html
import re
from typing import Annotated

import anyio
from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.bootstrap.container import Container
from app.bootstrap.demo import VENUE_TZ, DemoControls
from app.modules.delivery.domain.redemption import ExpiredRedemptionCode, UnknownRedemptionCode

router = APIRouter(tags=["redeem"])
_CODE = re.compile(r"^[A-Za-z0-9]{4,12}$")


class RedeemBody(BaseModel):
    nonce: Annotated[str, Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]


def _controls(request: Request) -> DemoControls:
    container: Container = request.app.state.container
    if container.demo is None:
        raise HTTPException(status_code=404, detail="no offers here")
    return container.demo


def _checked(code: str) -> str:
    if not _CODE.match(code):
        raise HTTPException(status_code=404, detail="no such offer")
    return code.upper()


def _notice(status: int, title: str, detail: str) -> HTMLResponse:
    """A phone-readable dead end (unknown or expired code), never raw JSON."""
    return HTMLResponse(
        _NOTICE.replace("{title}", html.escape(title)).replace("{detail}", html.escape(detail)),
        status_code=status,
    )


_UNKNOWN = (404, "Esta oferta no existe", "Revisa el código o escanea el QR de la pantalla.")


@router.get("/r/{code}", response_class=HTMLResponse)
async def offer_page(request: Request, code: str) -> HTMLResponse:
    container: Container = request.app.state.container
    if container.demo is None or not _CODE.match(code):
        return _notice(*_UNKNOWN)
    offer = await anyio.to_thread.run_sync(container.demo.offer_for, code.upper())
    if offer is None:
        return _notice(*_UNKNOWN)
    # Instants are UTC; the shopper reads the store's wall clock (America/Santiago).
    expires = offer.expires_at.astimezone(VENUE_TZ).strftime("%H:%M")
    if not offer.is_open(container.clock.now_utc()):
        return _notice(410, "Esta oferta expiró", f"Era válida hasta las {expires}.")
    brands = " + ".join(html.escape(name) for name in offer.brand_names)
    text = html.escape(offer.offer_text or brands)
    return HTMLResponse(
        _PAGE.replace("{brands}", brands)
        .replace("{text}", text)
        .replace("{expires}", expires)
        .replace("{code}", html.escape(offer.code))
    )


@router.post("/r/{code}/redeem")
async def redeem(
    request: Request, code: str, body: Annotated[RedeemBody, Body()]
) -> dict[str, object]:
    controls = _controls(request)
    try:
        message, created = await anyio.to_thread.run_sync(
            controls.redeem, _checked(code), body.nonce
        )
    except UnknownRedemptionCode as exc:
        raise HTTPException(status_code=404, detail=exc.msg) from exc
    except ExpiredRedemptionCode as exc:
        raise HTTPException(status_code=410, detail=exc.msg) from exc
    return {"redeemed": True, "new": created, "brands": message.brands, "code": message.code}


_PAGE = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Oferta {code}</title>
<style>
:root{--bg:#fafaf7;--fg:#1d1d1b;--muted:#6b6b66;--accent:#1f7a4d}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--fg:#ecebe6;--muted:#a3a29b;--accent:#5cc08a}}
body{margin:0;background:var(--bg);color:var(--fg);font:17px/1.45 system-ui,sans-serif}
main{max-width:28rem;margin:0 auto;padding:2rem 1rem}
h1{font-size:1.6rem;margin:.2rem 0}.muted{color:var(--muted)}
button{width:100%;font:inherit;font-weight:600;padding:1rem;border:0;border-radius:.8rem;
background:var(--accent);color:#fff;margin-top:1.5rem}
button[disabled]{opacity:.6}#done{font-weight:600;margin-top:1rem}
</style></head><body><main>
<p class="muted">{brands}</p><h1>{text}</h1>
<p class="muted">Código {code} · válido hasta las {expires}</p>
<button id="go">Canjear en caja</button><p id="done" role="status"></p>
<script>
(function(){
  var key="redeem-nonce", nonce;
  try{nonce=localStorage.getItem(key)}catch(e){}
  if(!nonce){nonce=(crypto.randomUUID?crypto.randomUUID():String(Math.random()).slice(2)+Date.now()).replace(/[^A-Za-z0-9_-]/g,"");
    try{localStorage.setItem(key,nonce)}catch(e){}}
  var go=document.getElementById("go"),done=document.getElementById("done");
  go.onclick=function(){go.disabled=true;
    fetch(location.pathname+"/redeem",{method:"POST",headers:{"content-type":"application/json"},
      body:JSON.stringify({nonce:nonce})}).then(function(r){
        if(r.status===410){done.textContent="Esta oferta expiró.";return}
        if(r.status===404){done.textContent="Esta oferta no existe.";return}
        if(!r.ok){done.textContent="No se pudo canjear. Muéstralo en caja.";go.disabled=false;return}
        return r.json().then(function(body){done.textContent=body.new===false
          ?"Ya canjeaste esta oferta en este teléfono. Muéstrala en caja."
          :"¡Listo! Muestra esta pantalla en caja.";});})
      .catch(function(){done.textContent="Sin conexión. Muéstralo en caja.";go.disabled=false});};
})();
</script></main></body></html>
"""

_NOTICE = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Oferta</title>
<style>
:root{--bg:#fafaf7;--fg:#1d1d1b;--muted:#6b6b66}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--fg:#ecebe6;--muted:#a3a29b}}
body{margin:0;background:var(--bg);color:var(--fg);font:17px/1.45 system-ui,sans-serif}
main{max-width:28rem;margin:0 auto;padding:2rem 1rem}
h1{font-size:1.6rem;margin:.2rem 0}.muted{color:var(--muted)}
</style></head><body><main>
<h1>{title}</h1><p class="muted">{detail}</p>
</main></body></html>
"""
