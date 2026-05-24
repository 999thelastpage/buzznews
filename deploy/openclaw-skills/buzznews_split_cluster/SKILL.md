---
name: buzznews-split-cluster
description: "Operator escape hatch to split a cluster — detaches specified raw_item IDs from one cluster into a new cluster. Use when two unrelated news events were incorrectly merged into one cluster."
---

# BuzzNews — Split Cluster

Detaches specific raw items from a cluster into a new separate cluster.

## Requirements

- BuzzNews CLI must be installed at `/opt/buzz-news/.venv/bin/python -m buzz_news`
- The `split-cluster` subcommand must be available

## Commands

```bash
# Split a cluster, detaching specific raw_item IDs
sudo -u buzz /opt/buzz-news/.venv/bin/python -m buzz_news split-cluster <cluster_id> --items <raw_item_id_1>,<raw_item_id_2>,<raw_item_id_3>

# Example: detach items 12345 and 67890 from cluster 999
sudo -u buzz /opt/buzz-news/.venv/bin/python -m buzz_news split-cluster 999 --items 12345,67890
```

## Arguments

- `cluster_id` — the ID of the cluster to split
- `--items` — comma-separated list of `raw_item` IDs to detach

## Notes

- Items not in `--items` stay in the original cluster
- A new cluster is created for the detached items
- The original cluster's counters (source_count, distinct_sources, etc.) are updated
- Use `buzznews_status` to verify the split worked correctly after running
