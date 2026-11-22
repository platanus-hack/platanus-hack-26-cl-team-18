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
from app.modules.delivery.domain.redemption import (
    ExpiredRedemptionCode,
    RedemptionLimitReached,
    UnknownRedemptionCode,
)

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
    except RedemptionLimitReached as exc:
        raise HTTPException(status_code=409, detail=exc.msg) from exc
    return {"redeemed": True, "new": created, "brands": message.brands, "code": message.code}


_PAGE = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Oferta {code}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600&family=Geist+Mono:wght@500&display=swap" rel="stylesheet">
<style>
:root{--bg:#fffdf8;--card:#ffffff;--fg:#13131e;--muted:#63636e;--edge:#ddd7cb;--ok:#186666;--ok-fill:#dff4eb;--ok-edge:#72ddaa}
@media (prefers-color-scheme:dark){:root{--bg:#13131e;--card:#1c1c28;--fg:#f4f2ec;--muted:#ababb7;--edge:#33333f;--ok:#72ddaa;--ok-fill:#16302a;--ok-edge:#2f6b55}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.45 "Instrument Sans",system-ui,sans-serif;-webkit-font-smoothing:antialiased}
main{max-width:26rem;margin:0 auto;padding:max(2rem,env(safe-area-inset-top)) 1.25rem 2rem;min-height:100vh;display:flex;flex-direction:column;gap:1.25rem}
.eyebrow{font:500 .72rem/1 "Geist Mono",ui-monospace,monospace;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin:0}
h1{font-size:2rem;line-height:1.1;letter-spacing:-.02em;font-weight:600;margin:0}
.muted{color:var(--muted);margin:0}
.card{background:var(--card);border:1px solid var(--edge);border-radius:14px;padding:1.25rem;display:flex;flex-direction:column;gap:.9rem}
.code{font:500 1.6rem/1 "Geist Mono",ui-monospace,monospace;letter-spacing:.14em;text-align:center;padding:1rem;border:1.5px dashed var(--edge);border-radius:10px}
button{width:100%;min-height:56px;font:600 1.05rem "Instrument Sans",system-ui,sans-serif;border:0;border-radius:12px;background:var(--fg);color:var(--bg);cursor:pointer}
button[disabled]{opacity:.55}
#done:empty{display:none}
#done{margin:0;padding:1rem;border-radius:12px;font-weight:600;background:var(--ok-fill);color:var(--ok);border:1px solid var(--ok-edge)}
#done.err{background:transparent;color:var(--fg);border-color:var(--edge)}
.fine{font-size:.8rem}
</style></head><body><main>
<p class="eyebrow">Oferta en tienda · {brands}</p>
<h1>{text}</h1>
<section class="card" aria-label="Tu código">
<p class="eyebrow">Muestra este código en caja</p>
<p class="code">{code}</p>
<p class="muted">Código {code} · válido hasta las {expires}</p>
</section>
<button id="go">Canjear en caja</button><p id="done" role="status"></p>
<p class="muted fine">Sin cuenta y sin datos personales: el código es todo lo que necesitas.</p>
<script>
(function(){
  var key="redeem-nonce", nonce;
  try{nonce=localStorage.getItem(key)}catch(e){}
  if(!nonce){nonce=(crypto.randomUUID?crypto.randomUUID():String(Math.random()).slice(2)+Date.now()).replace(/[^A-Za-z0-9_-]/g,"");
    try{localStorage.setItem(key,nonce)}catch(e){}}
  var go=document.getElementById("go"),done=document.getElementById("done");
  function say(text,bad){done.textContent=text;done.className=bad?"err":""}
  go.onclick=function(){go.disabled=true;
    fetch(location.pathname+"/redeem",{method:"POST",headers:{"content-type":"application/json"},
      body:JSON.stringify({nonce:nonce})}).then(function(r){
        if(r.status===410){say("Esta oferta expiró.",true);return}
        if(r.status===404){say("Esta oferta no existe.",true);return}
        if(r.status===409){say("Esta oferta ya se canjeó en todos los teléfonos que permite.",true);return}
        if(!r.ok){say("No se pudo canjear. Muéstralo en caja.",true);go.disabled=false;return}
        return r.json().then(function(body){go.hidden=true;say(body.new===false
          ?"Ya canjeaste esta oferta en este teléfono. Muéstrala en caja."
          :"¡Listo! Muestra esta pantalla en caja.");});})
      .catch(function(){say("Sin conexión. Muéstralo en caja.",true);go.disabled=false});};
})();
</script></main></body></html>
"""

_NOTICE = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Oferta</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600&family=Geist+Mono:wght@500&display=swap" rel="stylesheet">
<style>
:root{--bg:#fffdf8;--card:#ffffff;--fg:#13131e;--muted:#63636e;--edge:#ddd7cb;--ok:#186666;--ok-fill:#dff4eb;--ok-edge:#72ddaa}
@media (prefers-color-scheme:dark){:root{--bg:#13131e;--card:#1c1c28;--fg:#f4f2ec;--muted:#ababb7;--edge:#33333f;--ok:#72ddaa;--ok-fill:#16302a;--ok-edge:#2f6b55}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.45 "Instrument Sans",system-ui,sans-serif;-webkit-font-smoothing:antialiased}
main{max-width:26rem;margin:0 auto;padding:max(2rem,env(safe-area-inset-top)) 1.25rem 2rem;min-height:100vh;display:flex;flex-direction:column;gap:1.25rem}
.eyebrow{font:500 .72rem/1 "Geist Mono",ui-monospace,monospace;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin:0}
h1{font-size:2rem;line-height:1.1;letter-spacing:-.02em;font-weight:600;margin:0}
.muted{color:var(--muted);margin:0}
</style></head><body><main>
<p class="eyebrow">Oferta en tienda</p>
<h1>{title}</h1><p class="muted">{detail}</p>
</main></body></html>
"""
