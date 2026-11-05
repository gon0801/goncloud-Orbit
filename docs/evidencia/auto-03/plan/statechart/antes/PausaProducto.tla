---- MODULE PausaProducto ----
\* Un anuncio de producto con el plan decisiones-producto-01 construido (bloques H e I).
EXTENDS Naturals

VARIABLES interruptor, datos, mezclaCambio, amazon, fila, modo, autoria, cobros,
          regresado, envioSinVivo, envioConVenta, envioConMezcla, envioRegresado

vars == <<interruptor, datos, mezclaCambio, amazon, fila, modo, autoria, cobros,
          regresado, envioSinVivo, envioConVenta, envioConMezcla, envioRegresado>>
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
  /\ envioSinVivo = FALSE /\ envioConVenta = FALSE
  /\ envioConMezcla = FALSE /\ envioRegresado = FALSE

\* Ciclo diario: el producto gasto sin ventas y el anuncio esta activo. La clave del
\* anuncio esta libre (ninguna fila abierta ni veto vigente).
ElCicloPropone ==
  /\ interruptor # "apagado"
  /\ fila \in {"ninguna", "descartada", "fallida", "aplicada"}
  /\ amazon = "activo"
  /\ datos = "sin_ventas"
  /\ fila' = "espera"
  /\ modo' = IF interruptor = "vivo" THEN "vivo" ELSE "practica"
  /\ mezclaCambio' = FALSE
  /\ cobros' = 0
  /\ autoria' = "ninguna"
  /\ UNCHANGED <<interruptor, datos, amazon, regresado, historia>>

CambiasElInterruptor ==
  /\ \E v \in {"apagado", "practica", "vivo"} : v # interruptor /\ interruptor' = v
  /\ UNCHANGED <<datos, mezclaCambio, amazon, fila, modo, autoria, cobros, regresado, historia>>

LlegaUnaVenta ==
  /\ datos = "sin_ventas" /\ datos' = "con_venta"
  /\ UNCHANGED <<interruptor, mezclaCambio, amazon, fila, modo, autoria, cobros, regresado, historia>>

CambiaLaMezcla ==
  /\ Abierta /\ ~mezclaCambio /\ mezclaCambio' = TRUE
  /\ UNCHANGED <<interruptor, datos, amazon, fila, modo, autoria, cobros, regresado, historia>>

TuVetas ==
  /\ Abierta /\ fila' = "vetada"
  /\ UNCHANGED <<interruptor, datos, mezclaCambio, amazon, modo, autoria, cobros, regresado, historia>>

VenceTuVeto ==
  /\ fila = "vetada" /\ fila' = "ninguna"
  /\ UNCHANGED <<interruptor, datos, mezclaCambio, amazon, modo, autoria, cobros, regresado, historia>>

\* libera_vencidos solo toma filas en vivo; una fila de practica jamas se libera.
PasanLas48h ==
  /\ fila = "espera" /\ modo = "vivo" /\ fila' = "liberada"
  /\ UNCHANGED <<interruptor, datos, mezclaCambio, amazon, modo, autoria, cobros, regresado, historia>>

DatosBien == interruptor = "vivo" /\ datos = "sin_ventas" /\ ~mezclaCambio /\ amazon = "activo"

LaRevisionDescarta ==
  /\ fila = "liberada" /\ ~DatosBien /\ fila' = "descartada"
  /\ UNCHANGED <<interruptor, datos, mezclaCambio, amazon, modo, autoria, cobros, regresado, historia>>

\* Revalida, cobra la cuota, deja el registro y manda el PUT.
LaRevisionManda ==
  /\ fila = "liberada" /\ DatosBien
  /\ fila' = "enviando"
  /\ cobros' = cobros + 1
  /\ envioSinVivo' = (envioSinVivo \/ interruptor # "vivo")
  /\ envioConVenta' = (envioConVenta \/ datos # "sin_ventas")
  /\ envioConMezcla' = (envioConMezcla \/ mezclaCambio)
  /\ envioRegresado' = (envioRegresado \/ regresado)
  /\ UNCHANGED <<interruptor, datos, mezclaCambio, amazon, modo, autoria, regresado>>

AmazonConfirma ==
  /\ fila = "enviando" /\ fila' = "aplicada" /\ amazon' = "pausado" /\ autoria' = "confirmada"
  /\ UNCHANGED <<interruptor, datos, mezclaCambio, modo, cobros, regresado, historia>>

AmazonNoContesta ==
  /\ fila = "enviando" /\ fila' = "sin_respuesta"
  /\ \E a \in {"activo", "pausado"} : amazon' = a
  /\ UNCHANGED <<interruptor, datos, mezclaCambio, modo, autoria, cobros, regresado, historia>>

\* La reconciliacion cierra sin otro PUT ni otro cobro.
LaRevisionVePausado ==
  /\ fila = "sin_respuesta" /\ amazon = "pausado"
  /\ fila' = "aplicada" /\ autoria' = "adoptada"
  /\ UNCHANGED <<interruptor, datos, mezclaCambio, amazon, modo, cobros, regresado, historia>>

LaRevisionVeActivo ==
  /\ fila = "sin_respuesta" /\ amazon = "activo" /\ fila' = "fallida"
  /\ UNCHANGED <<interruptor, datos, mezclaCambio, amazon, modo, autoria, cobros, regresado, historia>>

TuLoRegresas ==
  /\ fila = "aplicada" /\ amazon = "pausado"
  /\ amazon' = "activo" /\ regresado' = TRUE
  /\ UNCHANGED <<interruptor, datos, mezclaCambio, fila, modo, autoria, cobros, historia>>

Next ==
  \/ ElCicloPropone \/ CambiasElInterruptor \/ LlegaUnaVenta \/ CambiaLaMezcla
  \/ TuVetas \/ VenceTuVeto \/ PasanLas48h \/ LaRevisionDescarta \/ LaRevisionManda
  \/ AmazonConfirma \/ AmazonNoContesta \/ LaRevisionVePausado \/ LaRevisionVeActivo
  \/ TuLoRegresas

Spec == Init /\ [][Next]_vars

\* Reglas que David aceptaria.
SoloEnVivo == ~envioSinVivo
NuncaConVenta == ~envioConVenta
NuncaConOtraMezcla == ~envioConMezcla
UnSoloCobro == cobros <= 1
PracticaNoTapaLaReal == ~(interruptor = "vivo" /\ fila = "espera" /\ modo = "practica")
NoRepausaLoQueRegresaste == ~envioRegresado
====
