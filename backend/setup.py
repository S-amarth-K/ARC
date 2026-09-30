"""
One command to build everything the backend needs:

    python -m backend.setup

1. generate the synthetic dataset           (Phase 1)  -> data/arc.db
2. train baselines + risk + task models    (Phase 2)  -> models/
3. back-fill event/feedback/calibration    (Phases 3+7) by running the real engine over history
"""
import time

from .data import generate_dataset
from .ml import train
from . import backfill


def main():
    t = time.time()
    print("== 1/3 Generating synthetic dataset ==")
    generate_dataset.main()
    print("\n== 2/3 Training intelligence modules ==")
    train.main()
    print("\n== 3/3 Back-filling history through the Arbitration Engine ==")
    backfill.main()
    print(f"\nSetup complete in {time.time() - t:.0f}s.")


if __name__ == "__main__":
    main()
