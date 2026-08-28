# Runtime dependency provenance

DocuVerify uses pinned packages on the tested Python 3.12 baseline. Existing
OpenCV, PyMuPDF, Pillow, RapidOCR and ONNX Runtime dependencies remain unchanged.

The core expansion adds:

| Package | Pinned version | Purpose | Licence | Python baseline |
| --- | --- | --- | --- | --- |
| `jsonschema` | 4.26.0 | Strict Draft 2020-12 profile validation | MIT | Python 3.10+; tested on 3.12.10 |
| `pyHanko` | 0.36.2 | Local PDF signature parsing and validation | MIT | Python 3.10+; tested on 3.12.10 |

pyHanko is invoked without network fetching and with an explicit local trust
store. Its transitive cryptographic libraries are installed from the pinned
top-level resolution; no system CUDA, driver or certificate-store setting is
changed.
