# Discovery: AI log triage (Photosphere → Log AI).
# AI log triage ("logwatch"): a local LLM reads journal warnings on a timer and
# sends anything worrying as a notification (urgent → flare, notable → aurora).
#
# Settings: state.json "logwatch" (Photosphere → Advanced → AI log triage) over
# the defaults in pkgs/logwatch-models.json; site.nix's helios.logwatch.* sets
# the backend/model when Photosphere leaves them on "site".
#
# It must never hurt routing:
#  - runs in helios-ai.slice: low CPU/IO weight, memory capped, first to be
#    killed under memory pressure (systemd-oomd + OOMScoreAdjust)
#  - the model is loaded only for each run (unless keepLoaded)
#  - helios-ai-guard checks CPU/memory pressure every minute and pauses the
#    whole thing for a few hours when the router is struggling
{ config, lib, pkgs, ... }:

let
  cfg = config.helios;
  lw = cfg.logwatch;
  sw = cfg.solarWind;
  catalog = lib.importJSON ../pkgs/logwatch-models.json;
  st = lib.recursiveUpdate catalog.defaults (cfg.state.logwatch or { });
  backend = if st.backend == "site" then lw.backend else st.backend;
  local = backend != "remote";
  preset = catalog.presets.${st.model} or null;
  # Presets live in /var/lib/llm (helios-ctl downloads and verifies them);
  # "custom" uses customPath; "site" keeps site.nix's helios.logwatch.model.
  modelPath = if preset != null then "/var/lib/llm/${preset.file}"
    else if st.model == "custom" && st.customPath != null then st.customPath else lw.model;
  memoryMB = if st.memoryMB != null then st.memoryMB else if preset != null then preset.ramMB else 2560;
  llmUrl = if local then "http://127.0.0.1:8090" else lw.remoteUrl;
  ctl = "${cfg.internal.helios-ctl}/bin/helios-ctl";

  # Lines matching these regexes are dropped before anything else
  noise = [
    "kernel: .*ACPI.*"
    "kernel: .*Spectre.*"
    "avahi-daemon.*: .*"
    "systemd\\[1\\]: .*Configuration file .* is marked executable"
  ];
  # Lines matching these always alert immediately (flare) — no AI involved
  critical = [
    "I/O error" "EXT4-fs error" "Buffer I/O" "SMART" "Out of memory" "oom-kill"
    "segfault" "general protection" "Machine Check" "mce:"
    "trunk0: Link is Down" "r8152.*disconnect" "USB disconnect"
    "nftables.*(error|failed)" "kea-dhcp4.*(ERROR|FATAL)"
    "Failed to start" "entered failed state"
    "Accepted (password|publickey) for"          # any successful SSH login
    "heliopause-trap: "                          # LAN device probing the router's admin ports
  ];
  toFile = name: lines: pkgs.writeText name (lib.concatStringsSep "\n" lines + "\n");

  logwatch = pkgs.writeShellApplication {
    name = "logwatch";
    runtimeInputs = with pkgs; [ jq curl gnugrep gnused gawk coreutils systemd inetutils ];
    text = builtins.readFile ../pkgs/logwatch.sh;
  };
in
lib.mkMerge [
# Always: a preset can be downloaded before logwatch is switched on. llama-cpp runs as this
# fixed account instead of a DynamicUser, so the models can be its own (0700 folder, 0400
# files) and nobody else's; ProtectSystem=strict keeps them read-only to it. z only touches
# what already exists.
{
  users.users.llm = { isSystemUser = true; group = "llm"; };
  users.groups.llm = { };
  systemd.tmpfiles.rules = [
    "z /var/lib/llm 0700 llm llm -"
    "z /var/lib/llm/*.gguf 0400 llm llm -"
  ];
}
(lib.mkIf (cfg.internal.on "logwatch") {
  # helios-ctl only accepts these; this catches a hand-edited state.json
  assertions = [{
    assertion = st.model != "custom" || st.customPath == null
      || builtins.match "/var/lib/llm/[A-Za-z0-9_-][A-Za-z0-9._-]*[.]gguf" st.customPath != null;
    message = "state.json logwatch: customPath must be a .gguf file directly in /var/lib/llm";
  }];
  hardware.graphics.enable = lib.mkIf (lw.backend == "vulkan") true;

  services.llama-cpp = lib.mkIf local {
    enable = true;
    package = if backend == "vulkan" then pkgs.llama-cpp-vulkan else pkgs.llama-cpp;
    model = modelPath;
    host = "127.0.0.1";
    port = 8090;
    extraFlags = [
      "--jinja"            # needed for chat_template_kwargs (disables Qwen3 "thinking")
      "-c" "4096"          # small context = small memory
      "-t" (toString st.threads)   # leave CPU cores for routing
      "--parallel" "1"
    ] ++ lib.optionals (backend == "vulkan") [ "-ngl" "99" ];
  };
  # Everything AI shares one low-priority slice with a memory ceiling
  systemd.slices.helios-ai = {
    description = "HeliOS AI log triage (lowest priority)";
    sliceConfig = {
      CPUWeight = 10;                  # default is 100: routing always wins
      IOWeight = 10;
      MemoryHigh = "${toString (memoryMB * 9 / 10)}M";   # reclaim/throttle before the hard cap
      MemoryMax = "${toString (memoryMB + 256)}M";
      ManagedOOMMemoryPressure = "kill";                 # systemd-oomd kills here first
      ManagedOOMMemoryPressureLimit = "60%";
    };
  };
  systemd.services.llama-cpp = lib.mkIf local {
    # On demand: started by each logwatch run, stopped after it (unless keepLoaded)
    wantedBy = lib.mkIf (!st.keepLoaded) (lib.mkForce [ ]);
    serviceConfig = {
      Slice = "helios-ai.slice";
      DynamicUser = lib.mkForce false;              # the fixed llm account owns the models
      # It only answers logwatch on 127.0.0.1:8090: even a hostile model file can't reach out
      IPAddressDeny = "any";
      IPAddressAllow = "localhost";
      User = "llm";
      Group = "llm";
      SupplementaryGroups = [ "render" "video" ];   # /dev/dri/renderD128
      CPUQuota = "${toString st.cpuPercent}%";
      Nice = 19;
      OOMScoreAdjust = 900;
      ExecCondition = "${ctl} logwatch-guard --check";    # not while paused
    };
    unitConfig.ConditionPathExists = modelPath;          # no model yet: don't crash-loop
  };

  systemd.services.logwatch = {
    description = "HeliOS AI triage of journal warnings";
    after = lib.optional local "llama-cpp.service" ++ [ "network-online.target" ];
    wants = [ "network-online.target" ] ++ lib.optional local "llama-cpp.service";
    environment = {
      STATE_DIR = "/var/lib/logwatch";
      LLM_URL = llmUrl;
      NOISE_FILE = toString (toFile "logwatch-noise" noise);
      CRITICAL_FILE = toString (toFile "logwatch-critical" critical);
      NTFY_BASE = if cfg.internal.on "notifications" then cfg.internal.solarWindUrl else "";
      TOPIC_FLARE = sw.topics.flare;
      TOPIC_AURORA = sw.topics.aurora;
      MAX_LINES = "60";
    };
    serviceConfig = {
      Type = "oneshot";
      Slice = "helios-ai.slice";
      ExecCondition = "${ctl} logwatch-guard --check";
      # Wait (up to 3 min) for the model to load when it's started on demand
      ExecStartPre = lib.mkIf local "${pkgs.bash}/bin/bash -c 'for i in $(seq 90); do ${pkgs.curl}/bin/curl -fsS -m 2 ${llmUrl}/health >/dev/null 2>&1 && exit 0; sleep 2; done; echo model did not load; exit 1'";
      ExecStart = lib.getExe logwatch;
      ExecStopPost = lib.mkIf (local && !st.keepLoaded) "+${pkgs.systemd}/bin/systemctl stop llama-cpp.service";
      StateDirectory = "logwatch";
      SupplementaryGroups = [ "systemd-journal" ];
      DynamicUser = true;
      # Solar Wind publish token, handed in without making it readable to the dynamic user's files
      LoadCredential = lib.mkIf (cfg.internal.on "notifications") [ "ntfy-token:${cfg.internal.solarWindTokenFile}" ];
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateTmp = true;
      NoNewPrivileges = true;
      Nice = 10;
    };
  };
  systemd.timers.logwatch = {
    wantedBy = [ "timers.target" ];
    timerConfig = { OnBootSec = "5min"; OnUnitActiveSec = "${toString st.interval}min"; Persistent = true; };
  };

  # The guard: pauses AI triage when routing is short of CPU or memory
  systemd.services.helios-ai-guard = {
    description = "HeliOS: pause AI log triage when the router is busy";
    serviceConfig = { Type = "oneshot"; ExecStart = "${ctl} logwatch-guard"; };
  };
  systemd.timers.helios-ai-guard = lib.mkIf st.guard.enabled {
    wantedBy = [ "timers.target" ];
    timerConfig = { OnBootSec = "2min"; OnUnitActiveSec = "1min"; AccuracySec = "10s"; };
  };
})
]
