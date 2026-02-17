Frontend:
Sends credentials
Stores token
Attaches token in headers
If you ever think “frontend login is enough”, stop backend work immediately.




/vm/connect/{vm_id} endpoint (app/api/vm.py)

This endpoint does NOT connect via SSH yet.

What it really does:

“Reserve this VM for me if nobody else is using it.”

User clicks "Open Terminal"
↓
POST /session/create/{vm_id}
↓
Receive session_id
↓
Open WebSocket /session/ws/{session_id}
↓
Stream input/output continuously
↓
User types commands freely
↓
Close WS when done
