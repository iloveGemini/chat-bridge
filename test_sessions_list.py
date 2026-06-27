import traceback
import sys
import os

# 确保能导入 routes
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

try:
    from routes.prompt_routes import _get_sessions_list
except ImportError as e:
    print(f"ImportError: {e}")
    sys.exit(1)

class MockHandlerWrapper:
    def __init__(self):
        self.wfile = self
    
    def write(self, data):
        print(f"Response body: {data}")
        
    def send_response(self, code):
        print(f"Response code: {code}")
    
    def send_header(self, key, value):
        pass
    
    def end_headers(self):
        pass

def main():
    h = MockHandlerWrapper()
    query = {}
    session = {}
    session_id = "test_session"
    
    try:
        _get_sessions_list(h, query, session, session_id)
    except Exception as e:
        print("Exception caught during _get_sessions_list:")
        traceback.print_exc()

if __name__ == "__main__":
    main()
