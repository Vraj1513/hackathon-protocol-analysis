import subprocess
import time
import yaml
import numpy as np
import pandas as pd
import re

# (The parse_log_file function remains the same as before)
def parse_log_file(filepath):
    """Reads a log file and returns a list of latencies in milliseconds."""
    latencies_ns = []
    log_pattern = re.compile(r"\[recv_time, size\]:\[(\d+), \d+\]\s+sender_addr:[\d\.]+\s+\[send_time, size\]:\[(\d+), \d+\]")
    try:
        with open(filepath, 'r') as f:
            for line in f:
                match = log_pattern.search(line)
                if match:
                    recv_time_ns, send_time_ns = map(int, match.groups())
                    if (latency_ns := recv_time_ns - send_time_ns) > 0:
                        latencies_ns.append(latency_ns)
    except FileNotFoundError:
        print(f"Warning: Log file not found at {filepath}. Skipping.")
        return []
    return [ns / 1_000_000 for ns in latencies_ns]

def run_command(command):
    """Runs a shell command and waits for it to complete."""
    print(f"Executing: {command}")
    result = subprocess.run(command, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error running command: {command}\n{result.stderr}")
    return result

def run_single_experiment(protocol, message_rate, team_config, cluster_id, mesh_config=None):
    """Configures, deploys, and runs a single test."""
    config_name = f"{protocol}"
    if mesh_config:
        config_name = f"{protocol}_{mesh_config['name']}"
    
    print(f"\n--- Configuring for {config_name} at {message_rate} msg/s ---")
    
    # 1. Modify the config file
    with open(team_config, 'r') as f:
        config = yaml.safe_load(f)
    
    config['protocol'] = protocol
    # If it's the optimum protocol, apply the mesh settings
    if protocol == 'optimum-p2p' and mesh_config:
        config['OPTIMUM_MESH_TARGET'] = mesh_config['target']
        config['OPTIMUM_MESH_MIN'] = mesh_config['min']
        config['OPTIMUM_MESH_MAX'] = mesh_config['max']
        
    with open(team_config, 'w') as f:
        yaml.dump(config, f)

    # 2. Redeploy the cluster
    run_command(f"make stop_and_remove_containers CLUSTER={cluster_id}")
    run_command(f"make upload_configs CLUSTER={cluster_id}")
    run_command(f"make deploy_clusters CLUSTER={cluster_id}")
    print("Waiting 30 seconds for network to stabilize...")
    time.sleep(30)

    # 3. Run the test
    log_filename = f"results/{config_name}_{message_rate}rps.log"
    subscriber_process = subprocess.Popen(f". scripts/subscribe.sh > {log_filename}", shell=True, executable="/bin/bash")
    time.sleep(5)
    
    print("Starting publisher...")
    run_command(f". scripts/publish.sh --rate {message_rate} --duration 60")
    subscriber_process.terminate()
    print("Experiment finished.")

    # 4. Parse results
    latencies = parse_log_file(log_filename)
    if not latencies:
        return {"p99_latency_ms": float('inf')}

    return {
        "avg_latency_ms": np.mean(latencies),
        "p99_latency_ms": np.percentile(latencies, 99),
        "messages_received": len(latencies)
    }

# --- Main Execution Logic ---
if __name__ == "__main__":
    TEAM_NUMBER = 4 # Make sure this is your team number
    YOUR_CONFIG_FILE = f"config_p2p/config_p2p_{chr(ord('a') + TEAM_NUMBER - 1)}.yml"
    YOUR_CLUSTER = f"p2p_nodes_cluster_{chr(ord('a') + TEAM_NUMBER - 1)}"

    rates_to_test = [10, 50, 100, 200, 400]
    results = []

    # Define the different mesh configurations to test
    mesh_configs = {
        "low_density": {"name": "low_density", "target": 3, "min": 2, "max": 5},
        "default_density": {"name": "default_density", "target": 6, "min": 3, "max": 12},
        "high_density": {"name": "high_density", "target": 10, "min": 8, "max": 18}
    }

    # Test GossipSub (once, as it has no mesh settings)
    for rate in rates_to_test:
        stats = run_single_experiment("gossipsub", rate, YOUR_CONFIG_FILE, YOUR_CLUSTER)
        result_row = {"protocol": "gossipsub", "config": "n/a", "rate_rps": rate, **stats}
        results.append(result_row)
        if stats["p99_latency_ms"] == float('inf'): break

    # Test OptimumP2P with each mesh configuration
    for config_name, mesh_params in mesh_configs.items():
        for rate in rates_to_test:
            stats = run_single_experiment("optimum-p2p", rate, YOUR_CONFIG_FILE, YOUR_CLUSTER, mesh_config=mesh_params)
            result_row = {"protocol": "optimum-p2p", "config": config_name, "rate_rps": rate, **stats}
            results.append(result_row)
            if stats["p99_latency_ms"] == float('inf'): break
    
    df = pd.DataFrame(results)
    df.to_csv("mesh_experiment_results.csv", index=False)
    print("\n--- All experiments complete! Results saved to mesh_experiment_results.csv ---")
    print(df)
