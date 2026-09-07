"""Run Phase 2 collection over whatever select_channels.py chose."""
import sys, pandas as pd
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect  # noqa: E402

SEL = Path(__file__).resolve().parents[1] / "data" / "processed" / "selected_channels.csv"

if __name__ == "__main__":
    if not SEL.exists():
        print("[collect_selected] selected_channels.csv missing - run select_channels.py")
        sys.exit(1)
    df = pd.read_csv(SEL)
    collect.run(df["channel_id"].tolist(),
                dict(zip(df["channel_id"], df["bucket"])))
