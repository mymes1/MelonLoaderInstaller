#!/usr/bin/env bash
set -euo pipefail

# Builds a verified, repaired LemonLoader ARM64 Android melon_data_repaired.zip
# directly from official LemonLoader fork sources + RepairPatches/apply_lemonloader_repairs.py.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="${1:-/tmp/lemon_build}"
OUT_ZIP="${2:-$PWD/melon_data_repaired.zip}"

mkdir -p "$WORK_DIR"
ML_DIR="$WORK_DIR/MelonLoader"

if [ ! -d "$ML_DIR/.git" ]; then
  echo "[1/7] Cloning official LemonLoader/MelonLoader (0.6.X) with submodules..."
  git clone --branch 0.6.X --recurse-submodules https://github.com/LemonLoader/MelonLoader.git "$ML_DIR"
else
  echo "[1/7] Updating existing MelonLoader checkout..."
  git -C "$ML_DIR" submodule update --init --recursive
fi

# MelonLoader's .gitmodules lists External/* forks, though only MonoMod has an active gitlink in 0.6.X;
# populate the remaining official fork submodules directly if missing.
[ -f "$ML_DIR/External/Il2CppInterop/Il2CppInterop.Runtime/Il2CppInterop.Runtime.csproj" ] || \
  git clone --depth 1 https://github.com/LemonLoader/Il2CppInterop.git "$ML_DIR/External/Il2CppInterop"
[ -f "$ML_DIR/External/HarmonyX/Harmony/Harmony.csproj" ] || \
  git clone --depth 1 https://github.com/LemonLoader/HarmonyX.git "$ML_DIR/External/HarmonyX"
[ -f "$ML_DIR/External/JNISharp/JNISharp/JNISharp.csproj" ] || \
  git clone --depth 1 https://github.com/LemonLoader/JNISharp.git "$ML_DIR/External/JNISharp"
[ -f "$ML_DIR/External/Cpp2IL/Cpp2IL.Core/Cpp2IL.Core.csproj" ] || \
  git clone --depth 1 --branch 2022.1.0-pre-release.16 https://github.com/SamboyCoding/Cpp2IL.git "$ML_DIR/External/Cpp2IL"

echo "[2/7] Applying verified LemonLoader source repairs..."
python3 "$SCRIPT_DIR/apply_lemonloader_repairs.py" "$ML_DIR"

echo "[3/7] Building External/MonoMod (net8.0 Release)..."
pushd "$ML_DIR/External/MonoMod" >/dev/null
python3 - <<'PYEOF'
import json
from pathlib import Path
gj = Path("global.json")
if gj.exists():
    data = json.loads(gj.read_text(encoding="utf-8"))
    if "sdk" in data:
        data["sdk"]["rollForward"] = "latestFeature"
    gj.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print("[patched] External/MonoMod/global.json rollForward -> latestFeature")
PYEOF
for tfm in net6.0 net8.0 netstandard2.0; do
  for proj in \
    src/MonoMod.RuntimeDetour/MonoMod.RuntimeDetour.csproj \
    src/MonoMod.Patcher/MonoMod.Patcher.csproj; do
    dotnet build "$proj" -c Release -f "$tfm" -noAutoRsp
  done
done
popd >/dev/null

echo "[4/7] Building MelonLoader (.NET 8 Core, NativeHost, SupportModules/Il2Cpp, Il2CppAssemblyGenerator)..."
pushd "$ML_DIR" >/dev/null
SOL_DIR="$ML_DIR/"
dotnet build MelonLoader/MelonLoader.csproj -c Release -f net8 -p:SolutionDir="$SOL_DIR"
dotnet build MelonLoader.NativeHost/MelonLoader.NativeHost.csproj -c Release -f net8 -p:SolutionDir="$SOL_DIR"
dotnet build Dependencies/SupportModules/Il2Cpp/Il2Cpp.csproj -c Release -f net8 -p:SolutionDir="$SOL_DIR"
dotnet build Dependencies/Il2CppAssemblyGenerator/Il2CppAssemblyGenerator.csproj -c Release -f net8 -p:SolutionDir="$SOL_DIR"
popd >/dev/null

echo "[5/7] Building Android ARM64 native binaries (libBootstrap.so, libmain.so) via Android NDK..."
NDK_DIR="${ANDROID_NDK_LATEST_HOME:-${ANDROID_NDK_HOME:-${ANDROID_NDK_ROOT:-}}}"
if [ -z "$NDK_DIR" ] && [ -n "${ANDROID_HOME:-}" ] && [ -d "$ANDROID_HOME/ndk" ]; then
  NDK_DIR="$(ls -d "$ANDROID_HOME"/ndk/* 2>/dev/null | sort -V | tail -n 1 || true)"
fi
if [ -z "$NDK_DIR" ] || [ ! -d "$NDK_DIR" ]; then
  echo "ERROR: Android NDK not found. Set ANDROID_NDK_HOME or ANDROID_NDK_LATEST_HOME." >&2
  exit 1
fi

TOOLCHAIN_BIN="$NDK_DIR/toolchains/llvm/prebuilt/linux-x86_64/bin"
export CC_aarch64_linux_android="$TOOLCHAIN_BIN/aarch64-linux-android24-clang"
export CXX_aarch64_linux_android="$TOOLCHAIN_BIN/aarch64-linux-android24-clang++"
export AR_aarch64_linux_android="$TOOLCHAIN_BIN/llvm-ar"
export CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER="$TOOLCHAIN_BIN/aarch64-linux-android24-clang"

rustup target add aarch64-linux-android
pushd "$ML_DIR" >/dev/null
cargo build --release --target aarch64-linux-android -p Bootstrap -p main
popd >/dev/null

echo "[6/7] Fetching official .NET 8.0.6 linux-bionic-arm64 runtime + BaseLibs..."
DOTNET_TAR="$WORK_DIR/dotnet-runtime-8.0.6-linux-bionic-arm64.tar.gz"
if [ ! -f "$DOTNET_TAR" ]; then
  curl -fsSL -o "$DOTNET_TAR" \
    "https://builds.dotnet.microsoft.com/dotnet/Runtime/8.0.6/dotnet-runtime-8.0.6-linux-bionic-arm64.tar.gz"
fi

STAGE="$WORK_DIR/stage"
rm -rf "$STAGE"
mkdir -p "$STAGE/MelonLoader/net8" \
         "$STAGE/MelonLoader/Dependencies/SupportModules" \
         "$STAGE/MelonLoader/Dependencies/Il2CppAssemblyGenerator" \
         "$STAGE/MelonLoader/Documentation" \
         "$STAGE/dotnet" \
         "$STAGE/native"

# Copy managed MelonLoader build outputs
cp -a "$ML_DIR/Output/Release/MelonLoader/net8/." "$STAGE/MelonLoader/net8/"
cp -a "$ML_DIR/Output/Release/MelonLoader/Dependencies/SupportModules/." "$STAGE/MelonLoader/Dependencies/SupportModules/"
cp -a "$ML_DIR/Output/Release/MelonLoader/Dependencies/Il2CppAssemblyGenerator/." "$STAGE/MelonLoader/Dependencies/Il2CppAssemblyGenerator/"
cp -a "$ML_DIR/BaseLibs/net6/." "$STAGE/MelonLoader/net8/"

# Ensure MonoMod net8.0 binaries are present in MelonLoader/net8
for dll in MonoMod.Core.dll MonoMod.ILHelpers.dll MonoMod.RuntimeDetour.dll MonoMod.Utils.dll MonoMod.Patcher.dll; do
  SRC_MM="$ML_DIR/External/MonoMod/artifacts/bin/${dll%.dll}/release_net8.0/$dll"
  if [ -f "$SRC_MM" ]; then
    cp -f "$SRC_MM" "$STAGE/MelonLoader/net8/$dll"
  fi
done

# Documentation
for doc in CHANGELOG.md LICENSE.md NOTICE.txt README.md; do
  [ -f "$ML_DIR/$doc" ] && cp -f "$ML_DIR/$doc" "$STAGE/MelonLoader/Documentation/"
done

# .NET 8.0.6 runtime + fixed GC libcoreclr + openssl
tar -xzf "$DOTNET_TAR" -C "$STAGE/dotnet"
cp -f "$ML_DIR/BaseLibs/dotnet_fixed_gc/libcoreclr.so" "$STAGE/dotnet/shared/Microsoft.NETCore.App/8.0.6/libcoreclr.so"
cp -f "$ML_DIR/BaseLibs/openssl/libssl.so" "$STAGE/dotnet/shared/Microsoft.NETCore.App/8.0.6/libssl.so"
cp -f "$ML_DIR/BaseLibs/openssl/libcrypto.so" "$STAGE/dotnet/shared/Microsoft.NETCore.App/8.0.6/libcrypto.so"

# Native ARM64 binaries (libBootstrap.so, libmain.so, libdobby.so)
cp -f "$ML_DIR/target/aarch64-linux-android/release/libBootstrap.so" "$STAGE/native/libBootstrap.so"
cp -f "$ML_DIR/target/aarch64-linux-android/release/libmain.so" "$STAGE/native/libmain.so"

DOBBY_SO="$(find "${CARGO_HOME:-$HOME/.cargo}/git/checkouts" -path "*/dobby_libraries/android/arm64/libdobby.so" | head -n 1 || true)"
if [ -z "$DOBBY_SO" ] || [ ! -f "$DOBBY_SO" ]; then
  echo "ERROR: Could not locate libdobby.so from dobby-sys Cargo checkout." >&2
  exit 1
fi
cp -f "$DOBBY_SO" "$STAGE/native/libdobby.so"

# Repair manifest with exact source commits & SHA-256 checksums
ML_SHA="$(git -C "$ML_DIR" rev-parse HEAD)"
INTEROP_SHA="$(git -C "$ML_DIR/External/Il2CppInterop" rev-parse HEAD)"
HARMONY_SHA="$(git -C "$ML_DIR/External/HarmonyX" rev-parse HEAD)"
JNI_SHA="$(git -C "$ML_DIR/External/JNISharp" rev-parse HEAD)"
MONOMOD_SHA="$(git -C "$ML_DIR/External/MonoMod" rev-parse HEAD)"
CPP2IL_SHA="$(git -C "$ML_DIR/External/Cpp2IL" rev-parse HEAD)"

python3 - "$STAGE" "$ML_SHA" "$INTEROP_SHA" "$HARMONY_SHA" "$JNI_SHA" "$MONOMOD_SHA" "$CPP2IL_SHA" <<'PYEOF'
import hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path

stage = Path(sys.argv[1])
ml_sha, interop_sha, harmony_sha, jni_sha, monomod_sha, cpp2il_sha = sys.argv[2:8]

key_files = [
    "MelonLoader/net8/MelonLoader.dll",
    "MelonLoader/net8/MelonLoader.NativeHost.dll",
    "MelonLoader/net8/Il2CppInterop.Runtime.dll",
    "MelonLoader/net8/Il2CppInterop.Common.dll",
    "MelonLoader/net8/Il2CppInterop.Generator.dll",
    "MelonLoader/net8/Il2CppInterop.HarmonySupport.dll",
    "MelonLoader/net8/0Harmony.dll",
    "MelonLoader/net8/JNISharp.dll",
    "MelonLoader/Dependencies/SupportModules/Il2Cpp.dll",
    "MelonLoader/Dependencies/Il2CppAssemblyGenerator/Il2CppAssemblyGenerator.dll",
    "native/libBootstrap.so",
    "native/libmain.so",
    "native/libdobby.so",
]

hashes = {}
for rel in key_files:
    p = stage / rel
    if p.exists():
        hashes[rel] = hashlib.sha256(p.read_bytes()).hexdigest()

manifest = {
    "repairToolVersion": "1.0.0",
    "repairedAtUtc": datetime.now(timezone.utc).isoformat(),
    "fixesApplied": [
        "DotnetAssemblyLoadContextFix: removed obsolete .NET 6 LoadFromPath/LoadFromStream hooks (fixes NullReferenceException: Null method for MelonLoader)",
        "HarmonyNullGuards: added null guards across DotnetAssemblyLoadContextFix, DotnetModHandlerRedirectionFix, InstancePatchFix, PatchShield, DetourContextDisposeFix, ProcessFix",
        "Il2CppInteropABIAlignment: rebuilt MelonLoader, SupportModules/Il2Cpp, Il2CppAssemblyGenerator, and Il2CppInterop together with unified AsmResolver 6.0.0-beta.5 (fixes MissingMethodException: RegisterTypeOptions.set_LogSuccess)",
        "AndroidSSLAndUnityStripping: includes LemonLoader SSL_CERT_DIR fix (1ddfecd) and Il2CppInterop ScriptingMethodInvoke_Hook (631db44)"
    ],
    "sourceCommits": {
        "LemonLoader/MelonLoader": ml_sha,
        "LemonLoader/Il2CppInterop": interop_sha,
        "LemonLoader/HarmonyX": harmony_sha,
        "LemonLoader/JNISharp": jni_sha,
        "LemonLoader/MonoMod": monomod_sha,
        "SamboyCoding/Cpp2IL": cpp2il_sha
    },
    "sha256": hashes
}
(stage / "MelonLoader/repair-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
PYEOF

echo "[7/7] Packaging $OUT_ZIP..."
OUT_DIR="$(dirname "$OUT_ZIP")"
mkdir -p "$OUT_DIR"
rm -f "$OUT_ZIP"
(cd "$STAGE" && zip -q -r "$OUT_ZIP" MelonLoader dotnet native)
echo "Done: $OUT_ZIP ($(du -h "$OUT_ZIP" | cut -f1))"
