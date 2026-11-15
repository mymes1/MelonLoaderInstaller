#!/usr/bin/env bash
set -euo pipefail

# Builds a verified, repaired MelonLoader v0.7.3 Open-Beta ARM64 Android bundle (melon_data_repaired.zip)
# by porting LemonLoader 0.7.0's NativeAOT Android bootstrap (libmain.so via .NET 9 + NDK)
# and in-process Cpp2IL onto official LavaGang/MelonLoader v0.7.3, with .NET 8.0.6 runtime.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="${1:-/tmp/lemon_073_build}"
OUT_ZIP="${2:-$PWD/melon_data_repaired.zip}"

mkdir -p "$WORK_DIR"
ML_DIR="$WORK_DIR/MelonLoader"
LEMON_070_DIR="$WORK_DIR/Lemon070"
LEMON_06X_DIR="$WORK_DIR/Lemon06X"

if [ ! -d "$ML_DIR/.git" ]; then
  echo "[1/7] Cloning official LavaGang/MelonLoader (v0.7.3)..."
  git clone --depth 1 --branch v0.7.3 https://github.com/LavaGang/MelonLoader.git "$ML_DIR"
else
  echo "[1/7] Updating existing LavaGang/MelonLoader v0.7.3 checkout..."
  git -C "$ML_DIR" fetch --depth 1 origin tag v0.7.3 || true
fi

if [ ! -d "$LEMON_070_DIR/.git" ]; then
  echo "      Cloning LemonLoader/MelonLoader (0.7.0) for NativeAOT Android JNI/static deps..."
  git clone --depth 1 --branch 0.7.0 https://github.com/LemonLoader/MelonLoader.git "$LEMON_070_DIR"
fi

if [ ! -d "$LEMON_06X_DIR/.git" ]; then
  echo "      Cloning LemonLoader/MelonLoader (0.6.X) for Android fixed-GC libcoreclr.so & OpenSSL..."
  git clone --depth 1 --branch 0.6.X https://github.com/LemonLoader/MelonLoader.git "$LEMON_06X_DIR"
fi

if [ ! -f "$ML_DIR/External/Cpp2IL/Cpp2IL.Core/Cpp2IL.Core.csproj" ]; then
  echo "      Cloning SamboyCoding/Cpp2IL (2022.1.0-pre-release.21)..."
  mkdir -p "$ML_DIR/External"
  git clone --depth 1 --branch 2022.1.0-pre-release.21 https://github.com/SamboyCoding/Cpp2IL.git "$ML_DIR/External/Cpp2IL"
fi

echo "[2/7] Applying LavaGang/MelonLoader v0.7.3 Open-Beta Android ARM64 port & crash/stage diagnostics..."
python3 "$SCRIPT_DIR/apply_melonloader_073_android.py" "$ML_DIR" "$LEMON_070_DIR"

echo "[3/7] Locating Android NDK..."
NDK_DIR="${ANDROID_NDK_LATEST_HOME:-${ANDROID_NDK_HOME:-${ANDROID_NDK_ROOT:-}}}"
if [ -z "$NDK_DIR" ] && [ -n "${ANDROID_HOME:-}" ] && [ -d "$ANDROID_HOME/ndk" ]; then
  NDK_DIR="$(ls -d "$ANDROID_HOME"/ndk/* 2>/dev/null | sort -V | tail -n 1 || true)"
fi
if [ -z "$NDK_DIR" ] || [ ! -d "$NDK_DIR" ]; then
  echo "ERROR: Android NDK not found. Set ANDROID_NDK_HOME, ANDROID_NDK_ROOT, or ANDROID_NDK_LATEST_HOME." >&2
  exit 1
fi
export ANDROID_NDK_ROOT="$NDK_DIR"
echo "      Using ANDROID_NDK_ROOT=$ANDROID_NDK_ROOT"

echo "[4/7] Building MelonLoader v0.7.3 managed assemblies (net8.0 Release, AndroidBuild=true)..."
pushd "$ML_DIR" >/dev/null
SOL_DIR="$ML_DIR/"
COMMON_PROPS=(
  "-c" "Release"
  "-p:AndroidBuild=true"
  "-p:ForceRID=linux-bionic-arm64"
  "-p:SolutionDir=$SOL_DIR"
  "-p:Version=0.7.3"
)

dotnet restore MelonLoader/MelonLoader.csproj "${COMMON_PROPS[@]}"
dotnet build --no-restore MelonLoader/MelonLoader.csproj -f net8.0 "${COMMON_PROPS[@]}"

for proj in \
  MelonLoader.NativeHost/MelonLoader.NativeHost.csproj \
  Dependencies/SupportModules/Il2Cpp/Il2Cpp.csproj \
  Dependencies/Il2CppAssemblyGenerator/Il2CppAssemblyGenerator.csproj \
  UnityUtilities/UnityEngine.Il2CppAssetBundleManager/UnityEngine.Il2CppAssetBundleManager.csproj \
  UnityUtilities/UnityEngine.Il2CppImageConversionManager/UnityEngine.Il2CppImageConversionManager.csproj; do
  dotnet restore "$proj" "${COMMON_PROPS[@]}"
  dotnet build --no-restore "$proj" -f net8.0 "${COMMON_PROPS[@]}"
done

echo "[5/7] Publishing NativeAOT MelonLoader.Bootstrap (libmain.so, linux-bionic-arm64 via .NET 9 + NDK)..."
dotnet restore MelonLoader.Bootstrap/MelonLoader.Bootstrap.csproj -r linux-bionic-arm64 -p:AndroidBuild=true -p:SolutionDir="$SOL_DIR"
dotnet publish --no-restore MelonLoader.Bootstrap/MelonLoader.Bootstrap.csproj \
  -c Release \
  -r linux-bionic-arm64 \
  -p:AndroidBuild=true \
  -p:ForceRID=linux-bionic-arm64 \
  -p:SolutionDir="$SOL_DIR" \
  -p:Version=0.7.3 \
  -p:NativeLib=Shared \
  -p:PublishDir="$ML_DIR/Output/Release/linux-bionic-arm64"
popd >/dev/null

echo "[6/7] Fetching official .NET 8.0.6 linux-bionic-arm64 runtime + fixed-GC libcoreclr.so & OpenSSL..."
DOTNET_TAR="$WORK_DIR/dotnet-runtime-8.0.6-linux-bionic-arm64.tar.gz"
if [ ! -f "$DOTNET_TAR" ]; then
  curl -fsSL -o "$DOTNET_TAR" \
    "https://builds.dotnet.microsoft.com/dotnet/Runtime/8.0.6/dotnet-runtime-8.0.6-linux-bionic-arm64.tar.gz"
fi

STAGE="$WORK_DIR/stage"
rm -rf "$STAGE"
mkdir -p "$STAGE/MelonLoader/net6" \
         "$STAGE/MelonLoader/Dependencies/SupportModules" \
         "$STAGE/MelonLoader/Dependencies/Il2CppAssemblyGenerator" \
         "$STAGE/MelonLoader/Dependencies/NetStandardPatches" \
         "$STAGE/MelonLoader/Documentation" \
         "$STAGE/dotnet" \
         "$STAGE/native"

OUT_ROOT="$ML_DIR/Output/Release/linux-bionic-arm64"

# Copy managed MelonLoader v0.7.3 outputs into MelonLoader/net6/ (MelonEnvironment.OurRuntimeName = net6)
cp -a "$OUT_ROOT/MelonLoader/net8.0/." "$STAGE/MelonLoader/net6/"
cp -a "$OUT_ROOT/MelonLoader/Dependencies/SupportModules/." "$STAGE/MelonLoader/Dependencies/SupportModules/"
cp -a "$OUT_ROOT/MelonLoader/Dependencies/Il2CppAssemblyGenerator/." "$STAGE/MelonLoader/Dependencies/Il2CppAssemblyGenerator/"

# Ensure NetStandardPatches & Documentation are populated
if [ -d "$ML_DIR/BaseLibs/NetStandardPatches" ]; then
  cp -a "$ML_DIR/BaseLibs/NetStandardPatches/." "$STAGE/MelonLoader/Dependencies/NetStandardPatches/"
fi
for doc in CHANGELOG.md LICENSE.md NOTICE.txt README.md; do
  [ -f "$ML_DIR/$doc" ] && cp -f "$ML_DIR/$doc" "$STAGE/MelonLoader/Documentation/"
done

# Ensure MelonLoader.runtimeconfig.json targets Microsoft.NETCore.App 8.0.6
cat > "$STAGE/MelonLoader/net6/MelonLoader.runtimeconfig.json" <<'JSON'
{
  "runtimeOptions": {
    "tfm": "net8.0",
    "framework": {
      "name": "Microsoft.NETCore.App",
      "version": "8.0.6"
    },
    "configProperties": {
      "System.Globalization.Invariant": true
    }
  }
}
JSON

# .NET 8.0.6 runtime + LemonLoader fixed-GC libcoreclr + OpenSSL
tar -xzf "$DOTNET_TAR" -C "$STAGE/dotnet"
cp -f "$LEMON_06X_DIR/BaseLibs/dotnet_fixed_gc/libcoreclr.so" "$STAGE/dotnet/shared/Microsoft.NETCore.App/8.0.6/libcoreclr.so"
cp -f "$LEMON_06X_DIR/BaseLibs/openssl/libssl.so" "$STAGE/dotnet/shared/Microsoft.NETCore.App/8.0.6/libssl.so"
cp -f "$LEMON_06X_DIR/BaseLibs/openssl/libcrypto.so" "$STAGE/dotnet/shared/Microsoft.NETCore.App/8.0.6/libcrypto.so"

# Locate NativeAOT libmain.so
LIBMAIN_CANDIDATE=""
for cand in \
  "$OUT_ROOT/libmain.so" \
  "$OUT_ROOT/MelonLoader.Bootstrap.so" \
  "$ML_DIR/MelonLoader.Bootstrap/bin/Release/net9/linux-bionic-arm64/publish/libmain.so" \
  "$ML_DIR/MelonLoader.Bootstrap/bin/Release/net9/linux-bionic-arm64/native/libmain.so"; do
  if [ -f "$cand" ]; then
    LIBMAIN_CANDIDATE="$cand"
    break
  fi
done
if [ -z "$LIBMAIN_CANDIDATE" ]; then
  LIBMAIN_CANDIDATE="$(find "$ML_DIR" -name '*libmain.so' -o -name 'MelonLoader.Bootstrap.so' | head -n 1 || true)"
fi
if [ -z "$LIBMAIN_CANDIDATE" ] || [ ! -f "$LIBMAIN_CANDIDATE" ]; then
  echo "ERROR: Could not locate NativeAOT libmain.so after publish." >&2
  find "$ML_DIR/Output" "$ML_DIR/MelonLoader.Bootstrap/bin" -maxdepth 6 2>/dev/null || true
  exit 1
fi
cp -f "$LIBMAIN_CANDIDATE" "$STAGE/native/libmain.so"

echo "=== Verifying built ARM64 NativeAOT libmain.so ==="
readelf -h -lW -d "$STAGE/native/libmain.so" | grep -E "Machine|LOAD|NEEDED|SONAME"
readelf -Ws "$STAGE/native/libmain.so" | grep -E "JNI_OnLoad|GetJavaVM|GetLoaderConfig|LogMsg" || true

# Build manifest with exact source commits & SHA-256 checksums
ML_SHA="$(git -C "$ML_DIR" rev-parse HEAD)"
LEMON_070_SHA="$(git -C "$LEMON_070_DIR" rev-parse HEAD)"
LEMON_06X_SHA="$(git -C "$LEMON_06X_DIR" rev-parse HEAD)"
CPP2IL_SHA="$(git -C "$ML_DIR/External/Cpp2IL" rev-parse HEAD)"

python3 - "$STAGE" "$ML_SHA" "$LEMON_070_SHA" "$LEMON_06X_SHA" "$CPP2IL_SHA" <<'PYEOF'
import hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path

stage = Path(sys.argv[1])
ml_sha, lemon_070_sha, lemon_06x_sha, cpp2il_sha = sys.argv[2:6]

key_files = [
    "MelonLoader/net6/MelonLoader.dll",
    "MelonLoader/net6/MelonLoader.NativeHost.dll",
    "MelonLoader/net6/Il2CppInterop.Runtime.dll",
    "MelonLoader/net6/Il2CppInterop.Common.dll",
    "MelonLoader/net6/Il2CppInterop.Generator.dll",
    "MelonLoader/net6/Il2CppInterop.HarmonySupport.dll",
    "MelonLoader/net6/0Harmony.dll",
    "MelonLoader/net6/MonoMod.RuntimeDetour.dll",
    "MelonLoader/Dependencies/SupportModules/Il2Cpp.dll",
    "MelonLoader/Dependencies/Il2CppAssemblyGenerator/Il2CppAssemblyGenerator.dll",
    "MelonLoader/Dependencies/Il2CppAssemblyGenerator/Cpp2IL.Core.dll",
    "native/libmain.so",
]

hashes = {}
for rel in key_files:
    p = stage / rel
    if p.exists():
        hashes[rel] = hashlib.sha256(p.read_bytes()).hexdigest()

manifest = {
    "bundleFormat": "MelonLoader-0.7.3-OpenBeta-Android-NativeAOT",
    "melonLoaderVersion": "0.7.3 Open-Beta",
    "repairedAtUtc": datetime.now(timezone.utc).isoformat(),
    "fixesApplied": [
        "MelonLoaderV073OpenBetaAndroidPort: LavaGang/MelonLoader v0.7.3 built for linux-bionic-arm64 with LemonLoader 0.7.0 NativeAOT single-libmain.so bootstrap (.NET 9 + NDK)",
        "InProcessCpp2IL21: SamboyCoding/Cpp2IL 2022.1.0-pre-release.21 + AssetRipper.Primitives 3.2.0 + AsmResolver.DotNet 6.0.0-beta.5 aligned with MelonLoader v0.7.3",
        "AndroidNativeFixes: 16KB PT_LOAD alignment, SA_SIGINFO SIGSEGV/SIGABRT/SIGBUS handler, crash-surviving lemon_early_crash.log, libDir-aware dlopen, Android SDK>=30 permission guard, DOTNET_ROOT + SSL_CERT_DIR, and assets/lemon_stage.txt stage isolation"
    ],
    "sourceCommits": {
        "LavaGang/MelonLoader@v0.7.3": ml_sha,
        "LemonLoader/MelonLoader@0.7.0": lemon_070_sha,
        "LemonLoader/MelonLoader@0.6.X": lemon_06x_sha,
        "SamboyCoding/Cpp2IL@2022.1.0-pre-release.21": cpp2il_sha
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
