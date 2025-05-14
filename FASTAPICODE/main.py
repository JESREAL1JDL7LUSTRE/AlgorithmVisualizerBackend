import asyncio
import json
import logging
import os
import signal
import subprocess
import time
from typing import Dict, List, Optional
import threading

from fastapi import FastAPI, WebSocket, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Path to the C++ executable (ensure this points to your executable)
CPP_EXECUTABLE = ".\\algorithm\\main.exe"  # Ensure the path is consistent
GRAPH_PATH = ".\\algorithm\\SG.json"  # Path to the graph file

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
algorithm_process: Optional[subprocess.Popen] = None
process_lock = threading.Lock()

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
    
    with process_lock:
        if algorithm_process:
            # Stop existing process first
            await stop_algorithm_process()
            
        # Build command with parameters
        graph_path = ".\\algorithm\\SG.json"
        # Ensure speed parameter is properly handled
        speed_param = max(0.1, min(3.0, request.speed))
        cmd = [CPP_EXECUTABLE, graph_path, str(speed_param)]
        logger.info(f"Running command: {' '.join(cmd)}")
        logger.info(f"Current Working Directory: {os.getcwd()}")
        logger.info(f"Executable exists: {os.path.exists(CPP_EXECUTABLE)}")
        logger.info(f"Graph file exists: {os.path.exists(graph_path)}")
        
        try:
            # Create subprocess with pipes for stdout and stderr
            algorithm_process = subprocess.Popen(
                cmd,
                cwd=os.getcwd(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # Line buffered
            )
            
            if algorithm_process is None:
                raise Exception("Failed to create subprocess")
                
            logger.info(f"Process created with PID: {algorithm_process.pid}")
            
            # Start tasks to read output in background
            asyncio.create_task(read_and_broadcast())
            asyncio.create_task(capture_stderr())
            
            return AlgorithmResponse(
                message="Algorithm started successfully",
                job_id=str(algorithm_process.pid)
            )
            
        except Exception as e:
            logger.error(f"Failed to start algorithm: {str(e)}")
            logger.error(f"Exception type: {type(e)}")
            logger.error(f"Exception details: {e.__dict__ if hasattr(e, '__dict__') else 'No details'}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to start algorithm: {str(e)}"
            )

async def capture_stderr():
    """Capture and log stderr output from the algorithm process"""
    global algorithm_process
    
    if not algorithm_process:
        logger.error("No algorithm process found in stderr capture")
        return
        
    logger.info(f"Starting to capture stderr from PID: {algorithm_process.pid}")
    
    try:
        while algorithm_process and not algorithm_process.poll():
            line = algorithm_process.stderr.readline()
            if not line:
                continue
                
            error_msg = line.strip()
            if error_msg:
                logger.error(f"Algorithm stderr output: {error_msg}")
                # Also broadcast error to clients
                error_update = {
                    "type": "error",
                    "message": error_msg
                }
                for connection in active_connections:
                    try:
                        await connection.send_text(json.dumps(error_update))
                    except Exception as e:
                        logger.error(f"Failed to send error to client: {str(e)}")
                
    except Exception as e:
        logger.error(f"Error capturing stderr: {str(e)}")
        logger.exception(e)  # This will print the full stack trace

@app.post("/stop-algorithm", response_model=AlgorithmResponse)
async def stop_algorithm():
    """Stop the running algorithm"""
    global algorithm_process
    
    with process_lock:
        if not algorithm_process:
            return AlgorithmResponse(message="No algorithm is running")
            
        await stop_algorithm_process()
        
        # Broadcast algorithm_stopped event to all clients
        stop_message = {
            "type": "algorithm_stopped",
            "message": "Algorithm stopped by user request"
        }
        for connection in active_connections:
            try:
                await connection.send_text(json.dumps(stop_message))
            except Exception as e:
                logger.error(f"Failed to send stop message to client: {str(e)}")
                
        return AlgorithmResponse(message="Algorithm stopped successfully")

async def stop_algorithm_process():
    """Helper function to stop the algorithm process"""
    global algorithm_process
    
    if not algorithm_process:
        return
        
    try:
        # Send termination signal
        logger.info(f"Stopping algorithm process (PID: {algorithm_process.pid})")
        
        with process_lock:
            if algorithm_process:
                # Try SIGTERM first
                try:
                    algorithm_process.terminate()
                    
                    # Wait briefly for graceful termination
                    try:
                        algorithm_process.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        # If still running after timeout, force kill
                        algorithm_process.kill()
                except Exception:
                    # Process already terminated
                    pass
                    
                # Wait for process to exit
                if algorithm_process.poll() is None:
                    algorithm_process.wait()
                    
                logger.info("Algorithm process stopped")
                algorithm_process = None
                
    except Exception as e:
        logger.error(f"Error stopping algorithm process: {str(e)}")
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
            # Wait for messages from the client
            data = await websocket.receive_text()
            logger.info(f"Received WebSocket message: {data}")
            
            # Process any commands received from the frontend
            try:
                message = json.loads(data)
                if message.get("command") == "stop":
                    logger.info("Received stop command via WebSocket")
                    await stop_algorithm()
            except json.JSONDecodeError:
                logger.warning(f"Received invalid JSON via WebSocket: {data}")
            
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
        logger.error("No algorithm process found")
        return
        
    logger.info(f"Starting to read algorithm output from PID: {algorithm_process.pid}")
    logger.info(f"Number of active WebSocket connections: {len(active_connections)}")
    
    try:
        while algorithm_process and not algorithm_process.poll():
            line = algorithm_process.stdout.readline()
            if not line:
                logger.debug("Empty line received from algorithm")
                continue
                
            # Parse and broadcast JSON update
            try:
                line_str = line.strip()
                if not line_str:
                    logger.debug("Empty string after stripping")
                    continue
                    
                logger.debug(f"Raw output from algorithm: {line_str}")
                update = json.loads(line_str)
                
                # Log updates (except very frequent ones to reduce noise)
                if update.get("type") not in ["node_visited", "edge_explored", "dfs_visit"]:
                    logger.info(f"Algorithm update: {update}")
                    
                # Broadcast to all connected clients
                broadcast_count = 0
                for connection in active_connections:
                    try:
                        await connection.send_text(json.dumps(update))
                        broadcast_count += 1
                    except Exception as e:
                        logger.error(f"Failed to send to a client: {str(e)}")
                
                logger.debug(f"Broadcasted update to {broadcast_count} clients")
                
                # If this is the final result, clean up the process
                if update.get("type") in ["algorithm_complete", "result"]:
                    with process_lock:
                        if algorithm_process:
                            algorithm_process.stdout.close()
                            algorithm_process.stderr.close()
                            algorithm_process = None
                            logger.info("Algorithm process cleaned up after completion")
                    
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON from algorithm: {line_str} - {str(e)}")
            except Exception as e:
                logger.error(f"Error broadcasting update: {str(e)}")
                
        logger.info("Algorithm process output stream ended")
        
        # Final cleanup if not already done
        with process_lock:
            if algorithm_process:
                return_code = algorithm_process.poll()
                logger.info(f"Process return code: {return_code if return_code is not None else 'None'}")
                
                # Close pipes and clean up
                algorithm_process.stdout.close()
                algorithm_process.stderr.close()
                algorithm_process = None
                logger.info("Algorithm process cleaned up")
                
                # Broadcast completion if not already done
                completion_msg = {
                    "type": "algorithm_complete",
                    "exit_code": return_code if return_code is not None else 0,
                    "status": "success" if return_code == 0 or return_code is None else "error"
                }
                for connection in active_connections:
                    try:
                        await connection.send_text(json.dumps(completion_msg))
                    except Exception as e:
                        logger.error(f"Failed to send completion message: {str(e)}")
                    
    except Exception as e:
        logger.error(f"Error reading algorithm output: {str(e)}")
        logger.exception(e)  # This will print the full stack trace
    finally:
        # Final cleanup if something went wrong
        with process_lock:
            if algorithm_process:
                try:
                    algorithm_process.stdout.close()
                    algorithm_process.stderr.close()
                except:
                    pass
                algorithm_process = None
                logger.info("Algorithm process cleaned up in finally block")

# Shutdown event handler
@app.on_event("shutdown")
async def shutdown_event():
    global algorithm_process
    logger.info("Server shutting down, cleaning up resources")
    with process_lock:
        if algorithm_process:
            await stop_algorithm_process()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)