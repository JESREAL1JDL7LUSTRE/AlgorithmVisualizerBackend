#include "ownAlgo.h"
#include "graphloader.h"
#include <iostream>
#include <chrono>
#include <nlohmann/json.hpp>
#include <fstream>

using namespace std::chrono;
using json = nlohmann::json;

void emit_json_update(const json& update) {
    // Output JSON update to stdout (will be captured by FastAPI)
    std::cout << update.dump() << std::endl;
    // Make sure it's flushed immediately
    std::cout.flush();
}

// Modified Dinic class to emit JSON updates
class VisualizableDinic : public Dinic {
public:
    VisualizableDinic(int V) : Dinic(V), V(V) {}
    
    // Override maxFlow to add visualization events
    int maxFlow(int s, int t) {
        // Start timing
        auto start_time = high_resolution_clock::now();
        
        // Emit initialization event with graph structure
        json init_update = {
            {"type", "init"},
            {"nodes", json::array()},
            {"edges", json::array()}
        };
        
        // Add all nodes
        for (int i = 0; i < V; i++) {
            // Compute x,y positions in a circular layout
            double angle = 2 * M_PI * i / V;
            int radius = 200;
            int x = 250 + radius * cos(angle);
            int y = 250 + radius * sin(angle);
            
            init_update["nodes"].push_back({
                {"id", i},
                {"x", x},
                {"y", y}
            });
        }
        
        // Add all edges with initial flow=0
        for (int u = 0; u < V; u++) {
            for (const auto& e : adj[u]) {
                if (e.cap > 0) { // Only add forward edges
                    init_update["edges"].push_back({
                        {"source", u},
                        {"target", e.v},
                        {"capacity", e.cap},
                        {"flow", 0}
                    });
                }
            }
        }
        
        emit_json_update(init_update);
        
        // Run the algorithm
        int flow = 0;
        int iteration = 0;
        
        while (bfs(s, t)) {
            iteration++;
            
            // Reset ptr for new iteration
            fill(ptr.begin(), ptr.end(), 0);
            
            // Emit iteration start event
            json iter_update = {
                {"type", "iteration_start"},
                {"iteration", iteration}
            };
            emit_json_update(iter_update);
            
            int curr_flow;
            while ((curr_flow = dfs_with_viz(s, t, INF)) > 0) {
                flow += curr_flow;
                
                // Emit flow update
                json flow_update = {
                    {"type", "flow_update"},
                    {"iteration", iteration},
                    {"current_flow", flow},
                    {"augmentation", curr_flow}
                };
                emit_json_update(flow_update);
            }
        }
        
        // Calculate time taken
        auto end_time = high_resolution_clock::now();
        auto duration = duration_cast<milliseconds>(end_time - start_time);
        
        // Emit algorithm completion event
        json completion_update = {
            {"type", "algorithm_complete"},
            {"max_flow", flow},
            {"iterations", iteration},
            {"execution_time_ms", duration.count()}
        };
        emit_json_update(completion_update);
        
        return flow;
    }
    
    // Add public access to the adjacency list for visualization
    const std::vector<std::vector<Edge>>& getAdj() const {
        return adj;
    }
    
protected:
    // Override BFS to add visualization events
    bool bfs(int s, int t) {
        fill(level.begin(), level.end(), -1);
        level[s] = 0;
        
        std::queue<int> q;
        q.push(s);
        
        json bfs_start = {
            {"type", "bfs_start"},
            {"source", s},
            {"sink", t}
        };
        emit_json_update(bfs_start);
        
        while (!q.empty()) {
            int u = q.front();
            q.pop();
            
            // Emit node visit event
            json node_visit = {
                {"type", "node_visited"},
                {"node_id", u}
            };
            emit_json_update(node_visit);
            
            // Slow down for visualization purposes
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
            
            for (const Edge& e : adj[u]) {
                if (level[e.v] == -1 && e.flow < e.cap) {
                    level[e.v] = level[u] + 1;
                    q.push(e.v);
                    
                    // Emit edge exploration event
                    json edge_explore = {
                        {"type", "edge_explored"},
                        {"source", u},
                        {"target", e.v},
                        {"capacity", e.cap},
                        {"flow", e.flow},
                        {"residual", e.cap - e.flow}
                    };
                    emit_json_update(edge_explore);
                    
                    // Slow down slightly for edge exploration
                    std::this_thread::sleep_for(std::chrono::milliseconds(5));
                }
            }
        }
        
        bool path_exists = level[t] != -1;
        
        json bfs_complete = {
            {"type", "bfs_complete"},
            {"path_found", path_exists}
        };
        emit_json_update(bfs_complete);
        
        return path_exists;
    }
    
    // DFS with visualization
    int dfs_with_viz(int u, int t, int flow) {
        if (u == t) return flow;
        
        // Emit node visit in DFS
        json dfs_visit = {
            {"type", "dfs_visit"},
            {"node_id", u}
        };
        emit_json_update(dfs_visit);
        
        // Small delay for visualization
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
        
        for (int& i = ptr[u]; i < adj[u].size(); ++i) {
            Edge& e = adj[u][i];
            
            if (level[e.v] == level[u] + 1 && e.flow < e.cap) {
                // Emit edge examination
                json edge_examine = {
                    {"type", "edge_examined"},
                    {"source", u},
                    {"target", e.v},
                    {"capacity", e.cap},
                    {"flow", e.flow},
                    {"residual", e.cap - e.flow}
                };
                emit_json_update(edge_examine);
                
                int curr_flow = min(flow, e.cap - e.flow);
                int temp_flow = dfs_with_viz(e.v, t, curr_flow);
                
                if (temp_flow > 0) {
                    e.flow += temp_flow;
                    adj[e.v][e.rev].flow -= temp_flow;
                    
                    // Emit edge update
                    json edge_update = {
                        {"type", "edge_updated"},
                        {"source", u},
                        {"target", e.v},
                        {"capacity", e.cap},
                        {"flow", e.flow},
                        {"residual", e.cap - e.flow}
                    };
                    emit_json_update(edge_update);
                    
                    return temp_flow;
                }
            }
        }
        
        return 0;
    }
    
    // Store path information for visualization
    void reconstruct_path(int s, int t, std::vector<int>& path) {
        path.clear();
        if (level[t] == -1) return; // No path exists
        
        std::vector<bool> visited(V, false);
        std::vector<int> parent(V, -1);
        
        std::queue<int> q;
        q.push(s);
        visited[s] = true;
        
        while (!q.empty() && !visited[t]) {
            int u = q.front();
            q.pop();
            
            for (const Edge& e : adj[u]) {
                if (!visited[e.v] && e.flow < e.cap) {
                    visited[e.v] = true;
                    parent[e.v] = u;
                    q.push(e.v);
                }
            }
        }
        
        if (!visited[t]) return;
        
        // Reconstruct path from t to s
        int curr = t;
        path.push_back(curr);
        while (curr != s) {
            curr = parent[curr];
            path.push_back(curr);
        }
        
        // Reverse to get path from s to t
        std::reverse(path.begin(), path.end());
    }
    
    // Variables for visualization
    int V;
    using Dinic::adj;
    using Dinic::level;
    using Dinic::ptr;
};

int main(int argc, char* argv[]) {
    // Parse command line arguments (could be extended with more options)
    std::string filename = "SG.json";
    if (argc > 1) {
        filename = argv[1];
    }
    
    // Optional speed control
    int delay_factor = 1;
    if (argc > 2) {
        delay_factor = std::stoi(argv[2]);
    }
    
    Graph graph;
    
    // Load graph from JSON file
    if (!load_graph_from_json(filename, graph)) {
        json error = {
            {"type", "error"},
            {"message", "Failed to load graph from JSON file: " + filename}
        };
        emit_json_update(error);
        return 1;
    }
    
    // Find highest node ID to determine graph size
    int max_node_id = -1;
    for (const auto& pair : graph) {
        max_node_id = std::max(max_node_id, pair.first);
        for (const auto& edge : pair.second) {
            max_node_id = std::max(max_node_id, edge.to);
        }
    }
    
    // Graph size is max_node_id + 1
    int V = max_node_id + 1;
    
    // Create visualizable Dinic
    VisualizableDinic dinic(V);
    
    // Add edges to the Dinic algorithm
    for (const auto& pair : graph) {
        int u = pair.first;
        for (const auto& edge : pair.second) {
            dinic.addEdge(u, edge.to, edge.capacity);
        }
    }
    
    // Notify initialization is complete
    json ready = {
        {"type", "ready"},
        {"nodes", V},
        {"source", 0},
        {"sink", V-1}
    };
    emit_json_update(ready);
    
    // Run max flow algorithm with visualization
    try {
        int max_flow = dinic.maxFlow(0, V-1);
        
        json result = {
            {"type", "result"},
            {"max_flow", max_flow}
        };
        emit_json_update(result);
    } catch (const std::exception& e) {
        json error = {
            {"type", "error"},
            {"message", std::string("Error during algorithm execution: ") + e.what()}
        };
        emit_json_update(error);
        return 1;
    }
    
    return 0;
}