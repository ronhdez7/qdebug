# Upload qdebug to Qollab

This folder contains the Qollab `main.py` entry point and its minimal runtime
requirements. The runner uses Qollab's selected backend, injected by the
platform as the global `backend` object. It records a four-qubit GHZ circuit,
then checks an unchanged version and a control/target logic mutation using the
project's actual `qdebug` package. The `qdebug/` directory must be preserved in
the Qollab project file tree.

## Upload

Qollab's documented flow does not import a GitHub repository directly. The
Python modules must remain in a real `qdebug/` package folder because the source
uses standard relative imports such as `from .stats import ...`. Do not flatten
the package files into the project root or rewrite the relative imports.

In Qollab's Code file tree, create a folder named `qdebug` if needed, then upload
all `.py` files from the repository's `qdebug/` directory into it, including
`__init__.py`. Keep `main.py` and `requirements.txt` at the project root.
Remove the old flattened module copies from the project root after placing the
files in `qdebug/`; the root should not contain `backends.py`, `stats.py`, etc.

After changing qdebug code, rebuild the upload archive from the repository root
with:

```bash
python qollab/build_upload.py
```

This overwrites `qollab-upload.zip` with the current Qollab entry point,
requirements, and package source. Upload that ZIP to Qollab; it has the desired
project root layout: `main.py`, `requirements.txt`, and `qdebug/` beside each
other. The generated ZIP is ignored by Git. To choose another output path, pass
it as an argument, for example `python qollab/build_upload.py /tmp/qollab.zip`.
Do not include `.env`, API keys, `.venv`, tests, or local snapshots.

## Run

Choose a free local simulator first. Select **Run Project**, choose the
simulator, and start the run. Qollab's runner exposes the selected Qiskit
backend as `backend`; no provider setup or API key belongs in the project.
`shots` and `seed` are adjustable in the Run settings. The Qollab entry point
has been verified with the built-in and AWS Braket local simulators. Qollab's
remote Forte-1/IonQ backend currently fails with this adapter; it is not
required for the free local-simulator project. This is separate from qdebug's
direct IonQ backend, whose live tests pass.

The output checks the unchanged GHZ circuit (`PASS`) and a flipped control and
target (`LOGIC_BUG`). The selected backend determines the noisy counts; exact
ideal verdict probabilities are calculated locally by qdebug. This compact
runner demonstrates live project code, while the full 12-case demo and browser
lesson remain in the GitHub repository.