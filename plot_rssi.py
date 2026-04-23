#!/usr/bin/env python3
"""Plot raw and filtered RSSI values per receiver from a CSV log."""

from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt

CSV_PATH = Path("somelog.csv")
RAW_PLOT_PATH = Path("rssi_raw_by_receiver.png")
FILTERED_PLOT_PATH = Path("rssi_filtered_by_receiver.png")


def load_rssi_csv(path: Path) -> pd.DataFrame:
    """Load the RSSI CSV and normalize timestamps and numeric fields."""
    if not path.exists():
        raise FileNotFoundError(f"RSSI CSV not found: {path}")

    text = path.read_text(encoding="utf-8")
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    df = pd.read_csv(
        pd.io.common.StringIO(text),
        dtype={"uuid": str, "receiver_id": str},
        skip_blank_lines=True,
    )

    if "timestamp" not in df.columns:
        raise ValueError("CSV is missing required timestamp column")

    df = df.dropna(subset=["timestamp"])
    df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")

    for col in ["raw_rssi", "filtered_rssi", "average_rssi"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def plot_series(df: pd.DataFrame, value_column: str, title: str, output_path: Path) -> None:
    """Plot a time-series for each receiver."""
    fig, ax = plt.subplots(figsize=(12, 6))
    receivers = sorted(df["receiver_id"].dropna().unique())

    for receiver in receivers:
        receiver_df = df[df["receiver_id"] == receiver].sort_values(by="timestamp")
        if receiver_df.empty:
            continue
        ax.plot(
            receiver_df["timestamp"],
            receiver_df[value_column],
            marker="o",
            linestyle="-",
            label=receiver,
        )

    ax.set_title(title)
    ax.set_xlabel("Timestamp")
    ax.set_ylabel("RSSI (dBm)")
    ax.legend()
    ax.grid(True, linestyle="--", alpha=0.4)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(output_path, dpi=200)
    plt.close(fig)


def print_stddev_summary(df: pd.DataFrame) -> None:
    """Print the raw and filtered RSSI standard deviation for each receiver and user."""
    if df.empty:
        print("No data available for standard deviation summary.")
        return

    grouped = df.groupby(["receiver_id", "uuid"])
    summary = grouped[["raw_rssi", "filtered_rssi"]].std(ddof=0)

    print("\nRSSI standard deviation by receiver and user:")
    print("receiver_id, uuid, raw_rssi_std, filtered_rssi_std")
    for (receiver_id, uuid), row in summary.sort_index().iterrows():
        raw_std = row.get("raw_rssi")
        filt_std = row.get("filtered_rssi")
        raw_text = f"{raw_std:.2f}" if pd.notna(raw_std) else "nan"
        filt_text = f"{filt_std:.2f}" if pd.notna(filt_std) else "nan"
        print(f"{receiver_id}, {uuid}, {raw_text}, {filt_text}")


def main() -> None:
    df = load_rssi_csv(CSV_PATH)
    print(f"Loaded {len(df)} rows from {CSV_PATH}")
    print("Receivers:", sorted(df["receiver_id"].dropna().unique()))

    print_stddev_summary(df)

    plot_series(df, "raw_rssi", "Raw RSSI by Receiver", RAW_PLOT_PATH)
    print(f"Saved raw RSSI plot to {RAW_PLOT_PATH}")

    plot_series(df, "filtered_rssi", "Filtered RSSI by Receiver", FILTERED_PLOT_PATH)
    print(f"Saved filtered RSSI plot to {FILTERED_PLOT_PATH}")


if __name__ == "__main__":
    main()
