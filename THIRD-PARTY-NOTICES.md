# Third-party notices

ChurchBridge Audio Bench builds on third-party work. This file records what,
and under which terms.

The bench's own code is licensed separately — see [`LICENSE`](LICENSE). Nothing
here changes those terms, and they do not apply to the components below.

## DeepFilterNet — speech enhancement signal chain

- **Upstream:** https://github.com/Rikorose/DeepFilterNet
- **Copyright:** © 2021 Hendrik Schröter
- **License:** MIT (upstream offers MIT or Apache-2.0 at the user's option;
  this project elects MIT)
- **Full license text:** [`LICENSE-DEEPFILTERNET`](LICENSE-DEEPFILTERNET)

`core/BenchmarkDeepFilterNet3Processor.swift` and
`core/DeepFilterNet3_Streaming.swift` implement the DeepFilterNet3 streaming
signal chain — STFT analysis and synthesis, the ERB filterbank and its inverse,
deep-filter application, overlap-add memory, and running normalization — in
Swift against Accelerate. The architecture, coefficients, and processing order
are DeepFilterNet's; the Swift implementation is not.

Upstream asks that use of the DeepFilterNet3 model be cited:

> Schröter, H., Rosenkranz, T., Escalante-B., A. N., and Maier, A.
> "DeepFilterNet: Perceptually Motivated Real-Time Speech Enhancement."
> INTERSPEECH, 2023.

## soniqo/speech-swift — auxiliary-data loading

- **Upstream:** https://github.com/soniqo/speech-swift
- **License:** Apache License 2.0
- **Full license text:** [`LICENSE-APACHE-2.0`](LICENSE-APACHE-2.0)

Portions of `core/BenchmarkDeepFilterNet3Processor.swift` are derived from this
project: the `.npz` / `.npy` auxiliary-data loading, specifically `parseNpy`,
`loadAuxiliaryData`, and the `readUInt16` / `readUInt32` / `readUInt64` helpers,
including the ZIP64 handling used to read the model's auxiliary archive.

**That file has been modified from the original**, as required by section 4(b)
of the Apache License. The surrounding STFT, ERB filterbank, deep-filter
application, tuning, and streaming logic are not from this source.

The upstream project ships no `NOTICE` file, so there is none to reproduce here.

## DeepFilterNet3-CoreML — the model weights

- **Source:** https://huggingface.co/aufklarer/DeepFilterNet3-CoreML
- **Author:** aufklarer
- **License:** Apache-2.0
- **Base model:** `Rikorose/DeepFilterNet3`

The neural network itself is an INT8-palettized Core ML conversion of
DeepFilterNet3, published by a third party. The bench **downloads it at runtime**
and does not redistribute it — no model weights are committed to this
repository. Because it is fetched rather than shipped, the Apache-2.0
redistribution obligations do not attach to this repository; they attach
wherever the artifacts are actually served.

Anyone running the bench is fetching Apache-2.0 licensed material from Hugging
Face and should be aware of that.

## Apple sample code and documentation

The audio capture design follows Apple's published guidance and sample code for
`AVAudioEngine` voice processing. No Apple sample code is included in this
repository; it was used as reference material only.

## Test material

Sermon audio used as benchmark input is third-party copyrighted material and is
**not** included in this repository. The `reports/` tree, which contains
transcripts derived from that audio, is gitignored and has never been committed.
