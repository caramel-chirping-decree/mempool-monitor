#!/usr/bin/env node
/**
 * Mempool.space WebSocket Monitor
 * Monitors Bitcoin network events and triggers webhooks based on criteria.
 * Run: node mempool_monitor.js [--config config.json]
 */

const WebSocket = require('ws');
const https = require('https');
const http = require('http');
const fs = require('fs');

const DEFAULT_CONFIG = {
  mempool_url: 'wss://mempool.space/api/v1/ws',
  webhooks: {},
  triggers: [],
  reconnect_delay: 5000,
};

// Map of websocket message keys to canonical event names
const EVENT_MAP = {
  'blocks': 'block',
  'mempool': 'mempool', 
  'txs': 'tx',
  'transactions': 'tx',
  ' conversions': 'stats',
};

class MempoolMonitor {
  constructor(configPath = 'config.json') {
    this.configPath = configPath;
    this.config = this.loadConfig();
    this.ws = null;
    this.running = false;
    this.subscriptions = new Set();
  }

  loadConfig() {
    let config = { ...DEFAULT_CONFIG };
    if (fs.existsSync(this.configPath)) {
      try {
        const loaded = JSON.parse(fs.readFileSync(this.configPath, 'utf8'));
        config = { ...config, ...loaded };
      } catch (e) {
        console.error(`[ERROR] Failed to load config: ${e.message}`);
      }
    }
    return config;
  }

  getNested(obj, path) {
    if (!path) return obj;
    const keys = path.split('.');
    let val = obj;
    for (const key of keys) {
      if (val && typeof val === 'object') {
        val = val[key];
      } else {
        return undefined;
      }
    }
    return val;
  }

  evaluateRule(rule, eventData) {
    const value = this.getNested(eventData, rule.field || '');
    const op = rule.operator || '==';
    const target = rule.value;

    if (op === 'exists') return value !== undefined && value !== null;
    if (op === 'contains') return String(value || '').includes(String(target));
    if (op === '==') return value == target;
    if (op === '!=') return value != target;
    if (op === '>') return Number(value || 0) > Number(target);
    if (op === '<') return Number(value || 0) < Number(target);
    if (op === '>=') return Number(value || 0) >= Number(target);
    if (op === '<=') return Number(value || 0) <= Number(target);

    return false;
  }

  async sendWebhook(webhookName, payload) {
    const webhook = this.config.webhooks?.[webhookName];
    if (!webhook) {
      console.log(`[WARN] Webhook '${webhookName}' not found`);
      return;
    }

    const { url, method = 'POST', headers = {}, timeout = 10000 } = webhook;
    const data = JSON.stringify(payload);

    return new Promise((resolve, reject) => {
      const parsedUrl = new URL(url);
      const client = parsedUrl.protocol === 'https:' ? https : http;

      const req = client.request(url, {
        method,
        headers: { 'Content-Type': 'application/json', ...headers },
        timeout,
      }, (res) => {
        console.log(`[WEBHOOK] ${webhookName} -> ${url} : ${res.statusCode}`);
        resolve();
      });

      req.on('error', (e) => {
        console.error(`[ERROR] Webhook ${webhookName} failed: ${e.message}`);
        reject(e);
      });

      req.write(data);
      req.end();
    });
  }

  handleEvent(eventType, eventData) {
    for (const rule of this.config.triggers || []) {
      if (rule.event !== eventType) continue;

      if (this.evaluateRule(rule, eventData)) {
        console.log(`[TRIGGER] "${rule.name}" matched! Firing webhook...`);
        try {
          this.sendWebhook(rule.webhook, {
            event: eventType,
            rule: rule.name,
            data: eventData,
            timestamp: Date.now(),
          });
        } catch (e) {
          // Already logged
        }
      }
    }
  }

  subscribe(ws, event) {
    if (this.subscriptions.has(event)) return;
    
    // Mempool uses 'want' for general subscriptions or specific events
    const subMsg = JSON.stringify({ action: 'want', data: [event] });
    ws.send(subMsg);
    
    this.subscriptions.add(event);
    console.log(`[SUB] ${event}`);
  }

  connect() {
    const url = this.config.mempool_url;
    console.log(`[INFO] Connecting to ${url}...`);

    this.ws = new WebSocket(url);

    this.ws.on('open', () => {
      console.log('[INFO] Connected!');
      this.running = true;

      // Subscribe to events from triggers
      const events = new Set();
      for (const rule of this.config.triggers || []) {
        events.add(rule.event);
      }
      for (const event of events) {
        this.subscribe(this.ws, event);
      }
    });

    this.ws.on('message', (data) => {
      try {
        const msg = JSON.parse(data);
        
        // Check message for known event keys
        for (const [key, eventType] of Object.entries(EVENT_MAP)) {
          if (msg[key]) {
            // For blocks/txs, there may be an array; for mempool, it's an object
            const eventData = Array.isArray(msg[key]) ? { [key]: msg[key] } : msg[key];
            this.handleEvent(eventType, eventData);
          }
        }
        
      } catch (e) {
        // Ignore non-JSON
      }
    });

    this.ws.on('close', () => {
      console.log('[WARN] Disconnected, reconnecting...');
      this.running = false;
      setTimeout(() => this.connect(), this.config.reconnect_delay);
    });

    this.ws.on('error', (e) => {
      console.error(`[ERROR] ${e.message}`);
    });
  }

  start() {
    console.log(`[INFO] Mempool Monitor starting...`);
    console.log(`[INFO] Config: ${this.configPath}`);
    this.connect();
  }
}

// Main
const configPath = process.argv.find((a, i) => process.argv[i - 1] === '--config') || 'config.json';
const monitor = new MempoolMonitor(configPath);
monitor.start();
