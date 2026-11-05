---- MODULE PausaProductoCorregido ----
\* Igual que PausaProducto, con las filas I.7 (encender cierra la practica) y H.7 (gracia de 7 dias).
EXTENDS Naturals

VARIABLES interruptor, datos, mezclaCambio, amazon, fila, modo, autoria, cobros,
          regresado, gracia, envioSinVivo, envioConVenta, envioConMezcla, envioRegresado

vars == <<interruptor, datos, mezclaCambio, amazon, fila, modo, autoria, cobros,
          regresado, gracia, envioSinVivo, envioConVenta, envioConMezcla, envioRegresado>>
historia == <<envioSinVivo, envioConVenta, envioConMezcla, envioRegresado>>

Abierta == fila \in {"espera", "liberada"}

Init ==
  /\ interruptor = "apagado"
  /\ datos = "sin_ventas"
  /\ mezclaCambio = FALSE
  /\ amazon = "activo"
  /\ fila = "ninguna"
  /\ modo = "practica"
  /\ autoria = "ninguna"
  /\ cobros = 0
  /\ regresado = FALSE
  /\ gracia = FALSE
  /\ envioSinVivo = FALSE /\ envioConVenta = FALSE
  /\ envioConMezcla = FALSE /\ envioRegresado = FALSE

\* Ciclo diario: el producto gasto sin ventas y el anuncio esta activo. La clave del
\* anuncio esta libre (ninguna fila abierta ni veto vigente).
ElCicloPropone ==
  /\ interruptor # "apagado"
  /\ fila \in {"ninguna", "descartada", "fallida", "aplicada"}
  /\ amazon = "activo"
  /\ datos = "sin_ventas"
  /\ ~gracia
  /\ fila' = "espera"
  /\ modo' = IF interruptor = "vivo" THEN "vivo" ELSE "practica"
  /\ mezclaCambio' = FALSE
  /\ cobros' = 0
  /\ autoria' = "ninguna"
  /\ UNCHANGED <<interruptor, datos, amazon, regresado, gracia, historia>>

CambiasElInterruptor ==
  /\ \E v \in {"apagado", "practica", "vivo"} : v # interruptor /\ interruptor' = v
  /\ fila' = IF interruptor' = "vivo" /\ fila = "espera" /\ modo = "practica" THEN "descartada" ELSE fila
  /\ UNCHANGED <<datos, mezclaCambio, amazon, modo, autoria, cobros, regresado, gracia, historia>>

LlegaUnaVenta ==
  /\ datos = "sin_ventas" /\ datos' = "con_venta"
  /\ UNCHANGED <<interruptor, mezclaCambio, amazon, fila, modo, autoria, cobros, regresado, gracia, historia>>

CambiaLaMezcla ==
  /\ Abierta /\ ~mezclaCambio /\ mezclaCambio' = TRUE
  /\ UNCHANGED <<interruptor, datos, amazon, fila, modo, autoria, cobros, regresado, gracia, historia>>

TuVetas ==
  /\ Abierta /\ fila' = "vetada"
  /\ UNCHANGED <<interruptor, datos, mezclaCambio, amazon, modo, autoria, cobros, regresado, gracia, historia>>

VenceTuVeto ==
  /\ fila = "vetada" /\ fila' = "ninguna"
  /\ UNCHANGED <<interruptor, datos, mezclaCambio, amazon, modo, autoria, cobros, regresado, gracia, historia>>

\* libera_vencidos solo toma filas en vivo; una fila de practica jamas se libera.
PasanLas48h ==
  /\ fila = "espera" /\ modo = "vivo" /\ fila' = "liberada"
  /\ UNCHANGED <<interruptor, datos, mezclaCambio, amazon, modo, autoria, cobros, regresado, gracia, historia>>

DatosBien == interruptor = "vivo" /\ datos = "sin_ventas" /\ ~mezclaCambio /\ amazon = "activo" /\ ~gracia

LaRevisionDescarta ==
  /\ fila = "liberada" /\ ~DatosBien /\ fila' = "descartada"
  /\ UNCHANGED <<interruptor, datos, mezclaCambio, amazon, modo, autoria, cobros, regresado, gracia, historia>>

\* Revalida, cobra la cuota, deja el registro y manda el PUT.
LaRevisionManda ==
  /\ fila = "liberada" /\ DatosBien
  /\ fila' = "enviando"
  /\ cobros' = cobros + 1
  /\ envioSinVivo' = (envioSinVivo \/ interruptor # "vivo")
  /\ envioConVenta' = (envioConVenta \/ datos # "sin_ventas")
  /\ envioConMezcla' = (envioConMezcla \/ mezclaCambio)
  /\ envioRegresado' = (envioRegresado \/ gracia)
  /\ UNCHANGED <<interruptor, datos, mezclaCambio, amazon, modo, autoria, regresado, gracia>>

AmazonConfirma ==
  /\ fila = "enviando" /\ fila' = "aplicada" /\ amazon' = "pausado" /\ autoria' = "confirmada"
  /\ UNCHANGED <<interruptor, datos, mezclaCambio, modo, cobros, regresado, gracia, historia>>

AmazonNoContesta ==
  /\ fila = "enviando" /\ fila' = "sin_respuesta"
  /\ \E a \in {"activo", "pausado"} : amazon' = a
  /\ UNCHANGED <<interruptor, datos, mezclaCambio, modo, autoria, cobros, regresado, gracia, historia>>

\* La reconciliacion cierra sin otro PUT ni otro cobro.
LaRevisionVePausado ==
  /\ fila = "sin_respuesta" /\ amazon = "pausado"
  /\ fila' = "aplicada" /\ autoria' = "adoptada"
  /\ UNCHANGED <<interruptor, datos, mezclaCambio, amazon, modo, cobros, regresado, gracia, historia>>

LaRevisionVeActivo ==
  /\ fila = "sin_respuesta" /\ amazon = "activo" /\ fila' = "fallida"
  /\ UNCHANGED <<interruptor, datos, mezclaCambio, amazon, modo, autoria, cobros, regresado, gracia, historia>>

TuLoRegresas ==
  /\ fila = "aplicada" /\ amazon = "pausado"
  /\ amazon' = "activo" /\ regresado' = TRUE /\ gracia' = TRUE
  /\ UNCHANGED <<interruptor, datos, mezclaCambio, fila, modo, autoria, cobros, historia>>

\* La gracia de 7 dias (S17) se abre al regresarlo y se cierra al pasar el plazo.
PasanLos7Dias ==
  /\ gracia /\ gracia' = FALSE
  /\ UNCHANGED <<interruptor, datos, mezclaCambio, amazon, fila, modo, autoria, cobros, regresado, historia>>

Next ==
  \/ ElCicloPropone \/ CambiasElInterruptor \/ LlegaUnaVenta \/ CambiaLaMezcla
  \/ TuVetas \/ VenceTuVeto \/ PasanLas48h \/ LaRevisionDescarta \/ LaRevisionManda
  \/ AmazonConfirma \/ AmazonNoContesta \/ LaRevisionVePausado \/ LaRevisionVeActivo
  \/ TuLoRegresas \/ PasanLos7Dias

Spec == Init /\ [][Next]_vars

\* Reglas que David aceptaria.
SoloEnVivo == ~envioSinVivo
NuncaConVenta == ~envioConVenta
NuncaConOtraMezcla == ~envioConMezcla
UnSoloCobro == cobros <= 1
PracticaNoTapaLaReal == ~(interruptor = "vivo" /\ fila = "espera" /\ modo = "practica")
NoRepausaEnLaGracia == ~envioRegresado
====
