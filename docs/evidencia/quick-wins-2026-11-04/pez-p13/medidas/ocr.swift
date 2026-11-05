// OCR local auxiliar: las coincidencias se verifican despues en la imagen original.
import AppKit
import Vision

var salida: [[String: Any]] = []
for ruta in CommandLine.arguments.dropFirst() {
    guard let imagen = NSImage(contentsOfFile: ruta),
          let cg = imagen.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        continue
    }
    let solicitud = VNRecognizeTextRequest()
    solicitud.recognitionLevel = .accurate
    solicitud.recognitionLanguages = ["es", "en"]
    try VNImageRequestHandler(cgImage: cg).perform([solicitud])
    let texto = (solicitud.results ?? []).compactMap { $0.topCandidates(1).first?.string }
    salida.append(["archivo": ruta, "texto": texto])
}
let datos = try JSONSerialization.data(withJSONObject: salida, options: [.prettyPrinted, .sortedKeys])
FileHandle.standardOutput.write(datos)
