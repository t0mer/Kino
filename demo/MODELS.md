# Demo voice models

The narrated demo uses Kokoro-82M v1.0 (Apache-2.0), run locally on CPU through `kokoro-onnx`.
The model files are not committed (`demo/models/` is gitignored).

| File | sha256 |
|---|---|
| `kokoro-v1.0.onnx` | `7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5` |
| `voices-v1.0.bin` | `bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d` |

Source: `https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/<file>`

Settings: voice `af_heart`, `lang="en-us"`, `speed=1.0`.

Setup (Python 3.12):

```bash
python3.12 -m venv demo/.venv
demo/.venv/bin/pip install -r demo/requirements.txt
mkdir -p demo/models   # then download both files above into it
```
