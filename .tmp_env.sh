ls .venv/lib/python3.11/site-packages | grep -i -E "^(onnx|opencv|rapidocr|numpy|supervision|omegaconf|shapely|pyclip|pytest|ruff|torch|nvidia|zxing|yaml|pydantic)" 
python -c "import numpy; print(numpy.__version__)"
python -c "import cv2; print(cv2.__version__)"
python -c "import onnxruntime as o; print(o.__version__, o.get_available_providers())"
