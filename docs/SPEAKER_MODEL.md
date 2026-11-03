# CAM++ speaker model

## Supported artifact and license

Nexo supports this specific Sherpa 3D-Speaker export, not arbitrary 80-bin ONNX models:

- Model: `iic/speech_campplus_sv_zh-cn_16k-common` (Chinese, 16 kHz).
- [Release asset](https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx).
- Size: 28,281,138 bytes.
- SHA-256: `f682b514c05d947ee3fa91cd6ec6c5c7543479a128373fa29b1faedccd21fd11`.
- License: Apache License 2.0, explicitly declared by the [model card](https://modelscope.cn/api/v1/models/iic/speech_campplus_sv_zh-cn_16k-common/repo?Revision=master&FilePath=README.md).
- [3D-Speaker license](https://github.com/modelscope/3D-Speaker/blob/065629c313eaf1a01c65c640c46d77e61e9607b4/LICENSE).

The checksum was computed locally from the HTTPS release asset. The upstream release API supplied no digest; this is not an independently signed checksum. Retain the model card, license and provenance alongside the installed file.

Default location: `~/.config/nexo-auth/models/speaker_campplus.onnx`. The models directory must be `0700`, the model and provenance/license files `0600`. Model loading checks SHA-256 before opening ONNX Runtime, then validates metadata and tensor contracts. A missing, different, corrupt or incompatible model disables enrollment. There is no generated embedding fallback.

## Frontend contract

Runtime dependency: **`kaldi-native-fbank==1.22.5`**, installed by both server dependency manifests. No Torch or Sherpa Python runtime is required.

Reference: Sherpa commit `935e5ded3d949a8b557ac44ef4dad03a7d2038e7`:

- [speaker-embedding-extractor-general-impl.h](https://github.com/k2-fsa/sherpa-onnx/blob/935e5ded3d949a8b557ac44ef4dad03a7d2038e7/sherpa-onnx/csrc/speaker-embedding-extractor-general-impl.h)
- [features.h](https://github.com/k2-fsa/sherpa-onnx/blob/935e5ded3d949a8b557ac44ef4dad03a7d2038e7/sherpa-onnx/csrc/features.h)
- [features.cc](https://github.com/k2-fsa/sherpa-onnx/blob/935e5ded3d949a8b557ac44ef4dad03a7d2038e7/sherpa-onnx/csrc/features.cc)

Input is little-endian PCM16 mono, 16 kHz. Check silence on the original integer amplitude, then convert to float32 and divide by **32768** before feature extraction (`normalize_samples=1`). `compute_fbank` accepts that normalized mono waveform.

Explicit feature settings:

| Setting | Value |
| --- | --- |
| Frame length / shift | 25 / 10 ms |
| Mel bins | 80 |
| Dither | 0 |
| Frame DC removal | true |
| Per-frame preemphasis | 0.97 |
| Window | Povey |
| FFT rounding | power of two |
| Boundary handling | `snip_edges=false`, flush via `input_finished()` |
| Frequency limits | 20 Hz to Nyquist minus 400 Hz (7600 Hz) |
| Mel implementation | Kaldi, not librosa |
| Features | log power Mel, no energy column |
| Normalization | subtract whole-utterance per-bin float32 mean |

Input tensor: `x`, float32 `[N,T,80]`, used with `N=1`. Output: `embedding`, float32 `[N,192]`, checked for finite values and nonzero norm, then L2-normalized. Required metadata includes `sample_rate=16000`, `normalize_samples=1`, `feature_normalize_type=global-mean`, `output_dim=192`, `framework=3d-speaker`, `language=Chinese`.

This deliberately follows the Sherpa export's reference frontend. Training-style torchaudio Kaldi defaults use different frequency limits and snipped boundaries. The previous NumPy Hamming/rounded-bin frontend was incompatible even after utterance mean subtraction.

## Profile compatibility and recovery

New profile JSON includes `profile_version=2`, frontend ID `sherpa-3dspeaker-knf-1.22.5-v1`, exact model checksum, and embedding dimension 192. Explicitly injected test sessions have a separate unvalidated model identity and cannot produce profiles compatible with the validated production model.

An existing `.npy` remains enrolled, including when its metadata is missing or unreadable. Verification fails closed when metadata is legacy, corrupt or mismatched. `get_metadata()` returns `status=reenrollment_required` and an Indonesian message instructing the user to confirm deletion and enroll again. Reading/verifying never migrates, deletes or rewrites old profiles. Use the authenticated, explicit voiceprint UI to delete and re-enroll; do not silently remove files to restore open mode.

## Provisioning and lifecycle

Install server requirements before deploying the source:

```sh
cd server
.venv/bin/python -m pip install -r requirements.txt
PYTHONPATH=src .venv/bin/python -m pytest tests/ -q -o asyncio_default_fixture_loop_scope=function
```

Provisioning sequence for another host:

1. Download the exact release asset into a private staging directory. Verify the size and checksum above before ONNX load.
2. Run the frontend parity tests and instantiate `SpeakerVerifier(model_path=staged_path)`; require `is_available()` and successful finite 192-dimensional extraction from a synthetic PCM sample. Do not enroll the synthetic sample.
3. Stage the validated bytes using `tempfile.mkstemp` in the destination models directory, flush/fsync, set `0600`, then `os.replace` to the default model path. Set the directory to `0700`. Do not overwrite an existing different model without reviewing compatibility.
4. Retain the Apache license, model card and a provenance JSON containing source URL, SHA-256, size, frontend ID and reference commit, all `0600`.
5. Coordinate deployment/restart explicitly. `_get_session()` caches unsuccessful model attempts: placing the file does **not** refresh an already-running verifier. A new process/verifier is required. Do not install this model while continuing to serve enrollments with the old NumPy frontend.

For this host the authorized installation completed after numerical parity passed. Files alongside the ONNX are `speaker_campplus.LICENSE`, `speaker_campplus.MODEL_CARD.md`, and `speaker_campplus.provenance.json`. No existing voiceprints were modified. The installation does not restart services.

## Verification evidence and limits

`server/tests/data/campplus_frontend_reference.npz` contains real float32 `[300,80]` output from the prior isolated probe's `reference_features`, using KNF 1.22.5 with the Sherpa settings above. It is not generated by production `compute_fbank`. Its input is the deterministic three-second synthetic chirp/tones/noise in `test_speaker_frontend_parity.synthetic_pcm`, seed `20261103`; no human recording is stored.

On the host CPU, the replacement frontend had RMSE **0.0** and maximum absolute error **0.0** against this reference. The real ONNX reference raw embedding norm was **23.04254722595215**. Production extraction matched its normalized embedding with maximum absolute error **0.0**, shape `[192]`, norm **1.0**. A fresh default-path verifier reported available. Directory/model modes were confirmed `0700`/`0600` and voiceprint checksums were unchanged.

Final regression suite after installation: **589 passed in 24.04s** using the command above. `pip check` returned `No broken requirements found.` and `git diff --check` passed. Coverage includes model hash/metadata/tensor rejection, normalized PCM input, golden feature parity, missing/corrupt/legacy/mismatched profile metadata, unchanged profile bytes, and real-model embedding parity. The real-model test runs when the pinned default artifact exists or `NEXO_TEST_SPEAKER_MODEL` points at it; otherwise only that test skips.

Synthetic inference proves numerical plumbing, not biometric accuracy. Threshold `0.65` is unchanged and uncalibrated for this microphone/language/noise. Validate with consented real enrollment and genuine/impostor samples before making discrimination, Indonesian generalization, liveness or spoof-resistance claims.
