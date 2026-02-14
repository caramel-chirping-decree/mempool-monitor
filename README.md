# Mempool Monitor

Bitcoin Mempool.space WebSocket monitor with configurable webhook triggers.

## Quickstart

```bash
# Clone
git clone https://github.com/caramel-chirping-decree/mempool-monitor.git
cd mempool-monitor

# Copy config
cp config.example.json config.json

# Edit config.json with your webhook URLs and triggers

# Run
python3 mempool_monitor.py --config config.json
```

## Configuration

Edit `config.json`:

```json
{
  "mempool_url": "wss://mempool.space/api/v1/ws",
  "reconnect_delay": 5,
  "webhooks": {
    "discord": {
      "url": "https://discord.com/api/webhooks/YOUR/WEBHOOK",
      "method": "POST"
    }
  },
  "triggers": [
    {
      "name": "new-block",
      "event": "block",
      "field": "blocks.0.height",
      "operator": "exists",
      "value": null,
      "webhook": "discord"
    }
  ]
}
```

### Trigger Options

| Field | Description |
|-------|-------------|
| `event` | `block`, `mempool`, or `tx` |
| `field` | Dot-path to value (e.g., `tx.vsize`) |
| `operator` | `==`, `!=`, `>`, `<`, `>=`, `<=`, `contains`, `exists` |
| `value` | Value to compare against |
| `webhook` | Name of webhook to fire |

### Events

- **block**: New Bitcoin blocks
- **mempool**: Mempool state changes (transaction count, fees)
- **tx**: New transactions in mempool

## Requirements

- Python 3.x (no dependencies — pure stdlib!)
- OR Node.js (uses `ws` package)

## License

MIT
