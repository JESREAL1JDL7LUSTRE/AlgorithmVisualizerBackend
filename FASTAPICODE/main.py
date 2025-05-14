import asyncio
import json
import logging
import os
import signal
import subprocess
from typing import Dict, List, Optional

from fastapi import FastAPI, WebSocket, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

aio = asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Path to the C++ executable (ensure this points to your executable)
CPP_EXECUTABLE = "./algorithm/main.exe"  # Ensure the path is consistent
GRAPH_PATH = "./algorithm/SG.json"  # Path to the graph file

process = subprocess.Popen(
    [CPP_EXECUTABLE, GRAPH_PATH, "1"],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
)
# Create FastAPI app
app = FastAPI(title="Flow Algorithm Visualizer")

# Add CORS middleware to allow requests from your Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Replace with your frontend URL in production, e.g. ["http://localhost:3000"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Store active connections and the algorithm process
active_connections: List[WebSocket] = []
algorithm_process: Optional[asyncio.subprocess.Process] = None
process_lock = asyncio.Lock()

# Models for API requests
class StartAlgorithmRequest(BaseModel):
    source: int = 0
    sink: Optional[int] = None
    algorithm: str = "Dinic"
    graph_type: str = "custom"
    graph_file: str = "SG.json"
    speed: float = 1.0

class AlgorithmResponse(BaseModel):
    message: str
    job_id: Optional[str] = None

@app.get("/")
async def root():
    return {"message": "Flow Algorithm Visualizer API"}

@app.get("/config")
async def get_config():
    """Get configuration options for the algorithm"""
    return {
        "algorithms": ["Dinic"],
        "graph_types": ["custom"],
        "predefined_graphs": ["SG.json"],
    }

@app.post("/start-algorithm", response_model=AlgorithmResponse)
async def start_algorithm(request: StartAlgorithmRequest):
    """Start the algorithm with the specified parameters"""
    global algorithm_process
    
    async with process_lock:
        if algorithm_process:
            # Stop existing process first
            await stop_algorithm_process()
            
        # Build command with parameters
        graph_path = "./algorithm/SG.json"
        cmd = f"{CPP_EXECUTABLE} {graph_path} {request.speed}"
        print("Current Working Directory: ", os.getcwd())
        
        try:
            # Check if executable exists
            if not os.path.exists(CPP_EXECUTABLE):
                logger.error(f"Executable not found: {CPP_EXECUTABLE}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Executable not found: {CPP_EXECUTABLE}"
                )
                
            # Check if graph file exists
            if not os.path.exists(graph_path):
                logger.error(f"Graph file not found: {graph_path}")
                raise HTTPException(
                    status_code=400,
                    detail=f"Graph file not found: {request.graph_file}"
                )
            
            # Start the algorithm process
            logger.info(f"Running command: {cmd}")
            logger.info(f"Current Working Directory: {os.getcwd()}")
            # Create subprocess with pipes for stdout and stderr
            algorithm_process = await aio.create_subprocess_exec(
                *cmd,
                cwd=os.getcwd(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            asyncio.create_task(read_and_broadcast())
            asyncio.create_task(capture_stderr())
            
            logger.info(f"Algorithm process started with PID: {algorithm_process.pid}")
            
            # Start task to read and broadcast output
            asyncio.create_task(read_and_broadcast())
            
            # Also start a task to capture stderr for debugging
            asyncio.create_task(capture_stderr())
            
            return AlgorithmResponse(
                message="Algorithm started successfully",
                job_id=str(algorithm_process.pid)
            )
            
        except Exception as e:
            logger.error(f"Failed to start algorithm: {str(e)}")
            raise HTTPException(
                status_code=500, 
                detail=f"Failed to start algorithm: {str(e)}"
            )

async def capture_stderr():
    """Capture and log stderr output from the algorithm process"""
    global algorithm_process
    
    if not algorithm_process or not algorithm_process.stderr:
        return
        
    try:
        while algorithm_process and algorithm_process.stderr:
            line = await algorithm_process.stderr.readline()
            if not line:
                break
                
            error_msg = line.decode('utf-8').strip()
            if error_msg:
                logger.error(f"Algorithm stderr: {error_msg}")
                
    except Exception as e:
        logger.error(f"Error capturing stderr: {str(e)}")

@app.post("/stop-algorithm", response_model=AlgorithmResponse)
async def stop_algorithm():
    """Stop the running algorithm"""
    async with process_lock:
        if not algorithm_process:
            return AlgorithmResponse(message="No algorithm is running")
            
        await stop_algorithm_process()
        return AlgorithmResponse(message="Algorithm stopped successfully")

async def stop_algorithm_process():
    """Helper function to stop the algorithm process"""
    global algorithm_process
    
    if not algorithm_process:
        return
        
    try:
        # Send termination signal
        logger.info(f"Stopping algorithm process (PID: {algorithm_process.pid})")
        
        # Try SIGTERM first
        try:
            os.kill(algorithm_process.pid, signal.SIGTERM)
            
            # Wait briefly for graceful termination
            try:
                await asyncio.wait_for(algorithm_process.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                # If still running after timeout, force kill
                os.kill(algorithm_process.pid, signal.SIGKILL)
        except ProcessLookupError:
            # Process already terminated
            pass
            
        # Wait for process to exit
        if algorithm_process.returncode is None:
            await algorithm_process.wait()
            
        logger.info("Algorithm process stopped")
        
    except Exception as e:
        logger.error(f"Error stopping algorithm process: {str(e)}")
    finally:
        algorithm_process = None

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time algorithm updates"""
    await websocket.accept()
    active_connections.append(websocket)
    
    try:
        logger.info(f"New WebSocket connection established")
        
        # Keep connection open until client disconnects
        while True:
            # Just wait for disconnection
            data = await websocket.receive_text()
            logger.debug(f"Received message: {data}")
            
            # Process any commands received from the frontend
            try:
                message = json.loads(data)
                if message.get("command") == "stop":
                    await stop_algorithm()
            except json.JSONDecodeError:
                pass
    except Exception as e:
        logger.error(f"WebSocket error: {str(e)}")
    finally:
        # Remove connection when client disconnects
        if websocket in active_connections:
            active_connections.remove(websocket)
        logger.info("WebSocket connection closed")
        
async def read_and_broadcast():
    """Read output from the algorithm process and broadcast to all WebSocket clients"""
    global algorithm_process
    
    if not algorithm_process:
        return
        
    logger.info("Starting to read algorithm output")
    
    try:
        while algorithm_process and algorithm_process.stdout:
            # Read line from stdout
            line = await algorithm_process.stdout.readline()
            if not line:
                break
                
            # Parse and broadcast JSON update
            try:
                line_str = line.decode('utf-8').strip()
                update = json.loads(line_str)
                
                # Log updates (except very frequent ones to reduce noise)
                if update.get("type") not in ["node_visited", "edge_explored", "dfs_visit"]:
                    logger.info(f"Algorithm update: {update['type']}")
                    
                # Broadcast to all connected clients
                for connection in active_connections:
                    await connection.send_text(json.dumps(update))
                    
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON from algorithm: {line} - {str(e)}")
            except Exception as e:
                logger.error(f"Error broadcasting update: {str(e)}")
                
        logger.info("Algorithm process output stream ended")
        
        # Check process status
        if algorithm_process:
            return_code = algorithm_process.returncode
            if return_code is not None:
                logger.info(f"Algorithm process exited with code {return_code}")
                
                # Broadcast completion if not already done
                completion_msg = {
                    "type": "algorithm_complete",
                    "exit_code": return_code,
                    "status": "success" if return_code == 0 else "error"
                }
                for connection in active_connections:
                    await connection.send_text(json.dumps(completion_msg))
                    
    except Exception as e:
        logger.error(f"Error reading algorithm output: {str(e)}")
    finally:
        # Ensure process is cleaned up
        async with process_lock:
            if algorithm_process:
                await stop_algorithm_process()

# Shutdown event handler
@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Server shutting down, cleaning up resources")
    async with process_lock:
        if algorithm_process:
            await stop_algorithm_process()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
