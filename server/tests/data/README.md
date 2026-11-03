# CAM++ frontend reference

`campplus_frontend_reference.npz` contains a float32 `features` array, shape
`[300, 80]`. It was generated from the pre-existing isolated compatibility
probe's `reference_features` function, not from production `compute_fbank`.

Generator: kaldi-native-fbank 1.22.5 with the Sherpa defaults pinned at commit
`935e5ded3d949a8b557ac44ef4dad03a7d2038e7`. The exact deterministic synthetic
PCM generator is `synthetic_pcm()` in `test_speaker_frontend_parity.py`.
Settings and upstream links are in `docs/SPEAKER_MODEL.md`.

No human speech, voiceprint, model bytes or credentials are stored here.
