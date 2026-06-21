import os
import sys

# Ensure backend root directory is in sys.path so 'app' module can be resolved
backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_root not in sys.path:
    sys.path.insert(0, backend_root)
