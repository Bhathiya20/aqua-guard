/*
 * Aqua Guard firmware secrets / site configuration TEMPLATE.
 *
 *   Do NOT copy this by hand.  Run:   python tools/generate_secrets.py
 *   It writes aqua_secrets.h (git-ignored) with fresh random keys AND writes the same command key to
 *   Aqua-Guard-WCS-UI/backend/.env, so the firmware and the backend can never drift apart.
 *
 *   This template defines AQUA_SECRETS_ARE_EXAMPLE: every firmware build refuses to compile with it, so an
 *   example/all-zero key can never end up in a real build.
 */
#pragma once
#define AQUA_SECRETS_ARE_EXAMPLE 1

// PSK: per-site SipHash key. Tags ONLY discovery and the pairing handshake frames (shared by every device
// of this site, compiled into one generic node binary).  Per-node data keys are derived after pairing.
#define AQUA_PSK  { 0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00 }
// PMK / LMK: ESP-NOW link-encryption keys (only used when AQUA_ESPNOW_ENCRYPT=1; see README for the peer limit).
#define AQUA_PMK  { 0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00 }
#define AQUA_LMK  { 0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00 }
// Site command key: authenticates + encrypts the backend <-> gateway commands. Same value as AQUAGUARD_CMD_KEY.
#define AQUA_CMD_KEY  { 0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00 }

// 1 = encrypt unicast ESP-NOW telemetry (CCMP). Default OFF: frames are already authenticated with per-node keys and flow
// readings need no confidentiality.  ESP-NOW supports only ~6-7 ENCRYPTED peers, so enabling this limits the node count.
#ifndef AQUA_ESPNOW_ENCRYPT
#define AQUA_ESPNOW_ENCRYPT 0
#endif

// Highest Wi-Fi channel your region allows (11 = FCC/US, 13 = EU/most of world)
#define AQUA_CH_MAX 13

// ---- Gateway DEFAULTS (fallback only; the real values are entered in the setup portal and kept in NVS) ----
#define AQUA_DEFAULT_MQTT_PORT          1883
#define AQUA_DEFAULT_MQTT_TOPIC_PREFIX  "waterleak/project001"
