from orchestrator import Orchestrator
from emulator import Emulator

orchestrator = Orchestrator()

plan = orchestrator.prepare({
    "num_ch": 4,
    "num_sn": 20,
    "duration": 20,
    "mode": "localhost",
    "seed": 810,
    "mobility": {
        "sn_mean_speed_mps": 10.0,
        "ch_mean_speed_mps": 20.0,
    },
})

plan.save("plan.json")

emulator = Emulator(
    "plan.json",
    "results",
)

emulator.run()

print("Emulation complete.")