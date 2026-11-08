"""Print the exact versions of Groundtruth's key packages (for pinning other environments)."""
from importlib.metadata import PackageNotFoundError, version

PKGS = ["torch", "torchvision", "open_clip_torch", "transformers", "huggingface_hub", "safetensors", "timm",
        "faiss-cpu", "numpy", "scikit-learn", "pyarrow", "shapely", "pyproj", "pillow", "rasterio",
        "pystac-client", "planetary-computer", "httpx", "pydantic", "fastapi", "uvicorn", "duckdb", "osmium"]
for p in PKGS:
    try:
        print(f"{p}=={version(p)}")
    except PackageNotFoundError:
        print(f"# {p} not installed")
