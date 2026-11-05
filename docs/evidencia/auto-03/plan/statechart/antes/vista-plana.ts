function anuncio(v: any): string {
  const otra = v.regresado;
  switch (v.fila) {
    case "espera":
      if (v.modo !== "vivo")
        return v.interruptor === "vivo"
          ? "Propuesta de práctica atorada, interruptor ya en vivo"
          : "Propuesta de práctica, nunca se ejecuta";
      return otra ? "Propuesta real otra vez, esperando 48 h" : "Propuesta real, esperando tus 48 h";
    case "vetada":
      return "Vetada por ti, 30 días";
    case "liberada":
      return otra ? "Revisando otra vez" : "Revisando datos frescos";
    case "enviando":
      return otra ? "Mandando la pausa otra vez" : "Mandando la pausa a Amazon";
    case "sin_respuesta":
      return "Amazon no contestó";
    case "aplicada":
      if (v.amazon === "activo") return "Lo regresaste, activo";
      return v.autoria === "adoptada" ? "Pausado, Orbit no sabe si fue él" : "Pausado por Orbit";
    default:
      return "Activo, sin propuesta";
  }
}

export const state = (v: any) => anuncio(v);

export const step = (accion: string, a: any, b: any): string | null => {
  switch (accion) {
    case "ElCicloPropone":
      return b.modo === "vivo"
        ? "el ciclo ve gasto sin ventas y propone pausar [interruptor en vivo]"
        : "el ciclo ve gasto sin ventas y propone pausar [interruptor en práctica]";
    case "CambiasElInterruptor":
      return b.interruptor === "vivo" ? "enciendes el interruptor" : "lo bajas a práctica o lo apagas";
    case "TuVetas":
      return "tú la vetas";
    case "VenceTuVeto":
      return "pasan los 30 días del veto";
    case "PasanLas48h":
      return "pasan 48 h sin veto";
    case "LaRevisionDescarta":
      return "la revisión ve una venta, otra mezcla o el interruptor apagado";
    case "LaRevisionManda":
      return "la revisión confirma y cobra la cuota";
    case "AmazonConfirma":
      return "Amazon confirma";
    case "AmazonNoContesta":
      return "Amazon no contesta";
    case "LaRevisionVePausado":
      return "la revisión lo encuentra pausado";
    case "LaRevisionVeActivo":
      return "la revisión lo encuentra activo";
    case "TuLoRegresas":
      return "lo regresas desde la pantalla";
    default:
      return null;
  }
};

export const title = "Pausa de un anuncio de producto con el plan construido";
