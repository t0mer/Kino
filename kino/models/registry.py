"""Pinned model registry: where each ONNX model comes from and its checksum."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSpec:
    """A downloadable model file.

    ``sha256`` is the checksum of the final model file (after extraction when
    ``archive_member`` is set).
    """

    url: str
    sha256: str
    license: str
    filename: str
    archive_member: str | None = None


_BUFFALO_L_ZIP = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"

MODELS: dict[str, ModelSpec] = {
    # SCRFD-10GF face detector, from the insightface buffalo_l pack (288 MB zip).
    "scrfd_10g": ModelSpec(
        url=_BUFFALO_L_ZIP,
        sha256="5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91",
        license="insightface pretrained models: non-commercial research use only",
        filename="det_10g.onnx",
        archive_member="det_10g.onnx",
    ),
    # AuraFace-v1 (fal/AuraFace-v1 on Hugging Face), 260,694,151 bytes.
    "auraface_glintr100": ModelSpec(
        url="https://huggingface.co/fal/AuraFace-v1/resolve/main/glintr100.onnx",
        sha256="a7933ea5330113b01c9b60351d8f4c33003f145d8470ac5f0e52ee2effe25c60",
        license="Apache-2.0",
        filename="glintr100.onnx",
    ),
    # buffalo_l ResNet-50 recognition model (WebFace600K), 174,383,860 bytes.
    "buffalo_l_r50": ModelSpec(
        url=_BUFFALO_L_ZIP,
        sha256="4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43",
        license="insightface pretrained models: non-commercial research use only",
        filename="w600k_r50.onnx",
        archive_member="w600k_r50.onnx",
    ),
}
