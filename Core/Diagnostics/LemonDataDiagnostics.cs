using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text;

namespace MelonLoader.Installer.Core.Diagnostics;

public enum DiagnosticSeverity
{
    Pass,
    Warning,
    Critical
}

public sealed record DiagnosticCheck(
    string Id,
    string Component,
    DiagnosticSeverity Severity,
    string Message,
    string? Detail = null);

public sealed class LemonDiagnosticReport
{
    public string SourcePath { get; init; } = "";
    public string SourceKind { get; init; } = "";
    public List<DiagnosticCheck> Checks { get; } = [];

    public bool HasCriticalIssues => Checks.Any(c => c.Severity == DiagnosticSeverity.Critical);
    public bool HasWarnings => Checks.Any(c => c.Severity == DiagnosticSeverity.Warning);
    public bool IsHealthy => !HasCriticalIssues;

    public bool HasNullMethodForMelonLoaderBug => Checks.Any(c =>
        c.Id == "ALC_QCALL_HOOKS" && c.Severity == DiagnosticSeverity.Critical);

    public bool HasRegisterTypeOptionsMismatch => Checks.Any(c =>
        c.Id == "REGISTER_TYPE_OPTIONS_LOG_SUCCESS" && c.Severity == DiagnosticSeverity.Critical);

    public string FormatReport()
    {
        var sb = new StringBuilder();
        sb.AppendLine("=== LemonLoader Dependency & ABI Diagnostic Report ===");
        sb.AppendLine($"Target:  {SourcePath} ({SourceKind})");
        sb.AppendLine($"Status:  {(IsHealthy ? (HasWarnings ? "HEALTHY (with warnings)" : "HEALTHY") : "REPAIR REQUIRED")}");
        sb.AppendLine();

        foreach (var check in Checks)
        {
            string badge = check.Severity switch
            {
                DiagnosticSeverity.Pass => "[PASS]",
                DiagnosticSeverity.Warning => "[WARN]",
                DiagnosticSeverity.Critical => "[FAIL]",
                _ => "[INFO]"
            };
            sb.AppendLine($"{badge} [{check.Component}] {check.Message}");
            if (!string.IsNullOrWhiteSpace(check.Detail))
                sb.AppendLine($"       {check.Detail}");
        }

        return sb.ToString();
    }
}

public static class LemonDataDiagnostics
{
    public static LemonDiagnosticReport Inspect(string path)
    {
        if (Directory.Exists(path))
            return InspectDirectory(path);

        if (!File.Exists(path))
            throw new FileNotFoundException("Target file not found for diagnostics.", path);

        bool isApk = path.EndsWith(".apk", StringComparison.OrdinalIgnoreCase);
        using FileStream fs = new(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        using ZipArchive archive = new(fs, ZipArchiveMode.Read);

        byte[]? TryReadZip(string entryPath)
        {
            var e = archive.Entries.FirstOrDefault(x =>
                x.FullName.Replace('\\', '/').Equals(entryPath, StringComparison.OrdinalIgnoreCase));
            if (e == null)
                return null;
            using var s = e.Open();
            using var ms = new MemoryStream();
            s.CopyTo(ms);
            return ms.ToArray();
        }

        string prefixMl = isApk ? "assets/MelonLoader/" : "MelonLoader/";
        string prefixDotnet = isApk ? "assets/dotnet/" : "dotnet/";
        string prefixNative = isApk ? "lib/arm64-v8a/" : "native/";

        var report = InspectEntries(
            path,
            isApk ? "Patched APK" : "melon_data.zip",
            TryReadZip,
            prefixMl,
            prefixDotnet,
            prefixNative,
            requireLibUnity: isApk);

        if (isApk)
        {
            var apkAnalysis = ApkAnalyzer.Analyze(path);
            if (apkAnalysis.ExtractNativeLibs == false)
            {
                report.Checks.Add(new DiagnosticCheck(
                    "ANDROID15_EXTRACT_NATIVE_LIBS",
                    "AndroidManifest.xml",
                    DiagnosticSeverity.Critical,
                    "android:extractNativeLibs is false in patched APK.",
                    "Android 15 rejects or fails dlopen() on compressed native .so entries unless extractNativeLibs=\"true\"."));
            }
            else if (apkAnalysis.ExtractNativeLibs == true)
            {
                report.Checks.Add(new DiagnosticCheck(
                    "ANDROID15_EXTRACT_NATIVE_LIBS",
                    "AndroidManifest.xml",
                    DiagnosticSeverity.Pass,
                    "android:extractNativeLibs is true (Android 15 compatible)."));
            }

            if (!apkAnalysis.HasV2SignatureBlock)
            {
                report.Checks.Add(new DiagnosticCheck(
                    "APK_SIGNATURE_V2",
                    "APK Signature",
                    DiagnosticSeverity.Critical,
                    "APK lacks APK Signature Scheme v2 block.",
                    "Android 11-15 requires valid v2 signing."));
            }
            else
            {
                report.Checks.Add(new DiagnosticCheck(
                    "APK_SIGNATURE_V2",
                    "APK Signature",
                    DiagnosticSeverity.Pass,
                    "APK Signature Scheme v2 block is present."));
            }
        }

        return report;
    }

    private static LemonDiagnosticReport InspectDirectory(string dirPath)
    {
        byte[]? TryReadFile(string relPath)
        {
            string full = Path.Combine(dirPath, relPath.Replace('/', Path.DirectorySeparatorChar));
            return File.Exists(full) ? File.ReadAllBytes(full) : null;
        }

        return InspectEntries(
            dirPath,
            "Extracted lemon_data directory",
            TryReadFile,
            "MelonLoader/",
            "dotnet/",
            "native/",
            requireLibUnity: false);
    }

    private static LemonDiagnosticReport InspectEntries(
        string sourcePath,
        string sourceKind,
        Func<string, byte[]?> readBytes,
        string mlPrefix,
        string dotnetPrefix,
        string nativePrefix,
        bool requireLibUnity)
    {
        var report = new LemonDiagnosticReport
        {
            SourcePath = sourcePath,
            SourceKind = sourceKind
        };

        // 1. MelonLoader.dll inspection (Null method for MelonLoader / DotnetAssemblyLoadContextFix)
        byte[]? mlDll = readBytes(mlPrefix + "net8/MelonLoader.dll");
        if (mlDll == null)
        {
            report.Checks.Add(new DiagnosticCheck(
                "MELONLOADER_DLL_MISSING",
                "MelonLoader.dll",
                DiagnosticSeverity.Critical,
                $"Missing {mlPrefix}net8/MelonLoader.dll"));
        }
        else
        {
            var mlInfo = DotNetMetadataInspector.Inspect(mlDll);
            if (!mlInfo.IsValidPe)
            {
                report.Checks.Add(new DiagnosticCheck(
                    "MELONLOADER_DLL_CORRUPT",
                    "MelonLoader.dll",
                    DiagnosticSeverity.Critical,
                    "MelonLoader.dll is not a valid .NET PE assembly.",
                    mlInfo.Error));
            }
            else
            {
                report.Checks.Add(new DiagnosticCheck(
                    "MELONLOADER_DLL_PRESENT",
                    "MelonLoader.dll",
                    DiagnosticSeverity.Pass,
                    $"Found MelonLoader.dll v{mlInfo.AssemblyVersion} ({mlInfo.TargetFramework ?? "net8"})"));

                bool hasLoadFromPathRef = mlInfo.MemberRefNames.Any(m =>
                    m.Contains("AssemblyLoadContext", StringComparison.Ordinal) &&
                    (m.Contains("LoadFromPath", StringComparison.Ordinal) || m.Contains("LoadFromStream", StringComparison.Ordinal)));

                bool hasLoadFromPathMethodRef = mlInfo.MemberRefNames.Any(m =>
                    m.EndsWith("::LoadFromPath", StringComparison.Ordinal) ||
                    m.EndsWith("::LoadFromStream", StringComparison.Ordinal));

                bool hasAlcFixStrings =
                    ElfHeaderInspector.ContainsAscii(mlDll, "LoadFromPath") ||
                    ElfHeaderInspector.ContainsAscii(mlDll, "LoadFromStream") ||
                    ElfHeaderInspector.ContainsUtf16(mlDll, "LoadFromPath") ||
                    ElfHeaderInspector.ContainsUtf16(mlDll, "LoadFromStream");

                if (hasLoadFromPathMethodRef || hasAlcFixStrings)
                {
                    report.Checks.Add(new DiagnosticCheck(
                        "ALC_QCALL_HOOKS",
                        "DotnetAssemblyLoadContextFix",
                        DiagnosticSeverity.Critical,
                        "Detected unpatched .NET 6 LoadFromPath/LoadFromStream references in MelonLoader.dll.",
                        "Causes System.NullReferenceException: Null method for MelonLoader on .NET 8 Android runtime."));
                }
                else
                {
                    report.Checks.Add(new DiagnosticCheck(
                        "ALC_QCALL_HOOKS",
                        "DotnetAssemblyLoadContextFix",
                        DiagnosticSeverity.Pass,
                        "Obsolete .NET 6 LoadFromPath/LoadFromStream hooks are absent (safe on .NET 8)."));
                }

                bool hasIl2CppInteropFixes = mlInfo.TypeNames.Contains("MelonLoader.Fixes.Il2CppInteropFixes");
                report.Checks.Add(new DiagnosticCheck(
                    "IL2CPP_INTEROP_FIXES",
                    "Il2CppInteropFixes",
                    hasIl2CppInteropFixes ? DiagnosticSeverity.Pass : DiagnosticSeverity.Warning,
                    hasIl2CppInteropFixes
                        ? "Il2CppInteropFixes present in MelonLoader.dll."
                        : "Il2CppInteropFixes missing in MelonLoader.dll."));
            }
        }

        // 2. Il2CppInterop.Runtime.dll + SupportModules/Il2Cpp.dll inspection (RegisterTypeOptions.set_LogSuccess)
        byte[]? interopRuntime = readBytes(mlPrefix + "net8/Il2CppInterop.Runtime.dll")
            ?? readBytes(mlPrefix + "Dependencies/SupportModules/Il2CppInterop.Runtime.dll");
        byte[]? il2cppSupportDll = readBytes(mlPrefix + "Dependencies/SupportModules/Il2Cpp.dll");

        MethodSignatureInfo? runtimeLogSuccessSig = null;
        if (interopRuntime == null)
        {
            report.Checks.Add(new DiagnosticCheck(
                "IL2CPP_INTEROP_RUNTIME_MISSING",
                "Il2CppInterop.Runtime.dll",
                DiagnosticSeverity.Critical,
                "Missing Il2CppInterop.Runtime.dll."));
        }
        else
        {
            var rtInfo = DotNetMetadataInspector.Inspect(
                interopRuntime,
                methodFilter: (type, method) =>
                    type == "Il2CppInterop.Runtime.Injection.RegisterTypeOptions" &&
                    method == "set_LogSuccess");

            runtimeLogSuccessSig = rtInfo.MatchingMethods.FirstOrDefault();
            if (!rtInfo.IsValidPe || runtimeLogSuccessSig == null)
            {
                report.Checks.Add(new DiagnosticCheck(
                    "REGISTER_TYPE_OPTIONS_LOG_SUCCESS",
                    "Il2CppInterop.Runtime.dll",
                    DiagnosticSeverity.Critical,
                    "Il2CppInterop.Runtime.Injection.RegisterTypeOptions.set_LogSuccess(bool) not found.",
                    "Causes MissingMethodException: Method not found: void Il2CppInterop.Runtime.Injection.RegisterTypeOptions.set_LogSuccess(bool)."));
            }
            else
            {
                string sigStr = $"{runtimeLogSuccessSig.ReturnType} ({string.Join(", ", runtimeLogSuccessSig.ParameterTypes)})";
                report.Checks.Add(new DiagnosticCheck(
                    "IL2CPP_INTEROP_RUNTIME_PRESENT",
                    "Il2CppInterop.Runtime.dll",
                    DiagnosticSeverity.Pass,
                    $"Il2CppInterop.Runtime.dll v{rtInfo.AssemblyVersion} exports RegisterTypeOptions.set_LogSuccess: {sigStr}"));
            }
        }

        if (il2cppSupportDll == null)
        {
            report.Checks.Add(new DiagnosticCheck(
                "SUPPORT_MODULE_IL2CPP_MISSING",
                "Il2Cpp.dll",
                DiagnosticSeverity.Critical,
                $"Missing {mlPrefix}Dependencies/SupportModules/Il2Cpp.dll."));
        }
        else
        {
            var smInfo = DotNetMetadataInspector.Inspect(
                il2cppSupportDll,
                memberRefFilter: (type, member) =>
                    type == "Il2CppInterop.Runtime.Injection.RegisterTypeOptions" &&
                    member == "set_LogSuccess");

            if (!smInfo.IsValidPe)
            {
                report.Checks.Add(new DiagnosticCheck(
                    "SUPPORT_MODULE_IL2CPP_CORRUPT",
                    "Il2Cpp.dll",
                    DiagnosticSeverity.Critical,
                    "SupportModules/Il2Cpp.dll is invalid PE metadata.",
                    smInfo.Error));
            }
            else if (runtimeLogSuccessSig != null)
            {
                var callSite = smInfo.MatchingMemberRefs.FirstOrDefault();
                if (callSite == null)
                {
                    report.Checks.Add(new DiagnosticCheck(
                        "REGISTER_TYPE_OPTIONS_LOG_SUCCESS",
                        "Il2Cpp.dll <-> Il2CppInterop.Runtime.dll",
                        DiagnosticSeverity.Warning,
                        "Il2Cpp.dll does not reference RegisterTypeOptions.set_LogSuccess directly."));
                }
                else
                {
                    bool returnMatches = string.Equals(callSite.ReturnType, runtimeLogSuccessSig.ReturnType, StringComparison.Ordinal);
                    bool paramsMatch = callSite.ParameterTypes.SequenceEqual(runtimeLogSuccessSig.ParameterTypes);
                    if (!returnMatches || !paramsMatch)
                    {
                        report.Checks.Add(new DiagnosticCheck(
                            "REGISTER_TYPE_OPTIONS_LOG_SUCCESS",
                            "Il2Cpp.dll <-> Il2CppInterop.Runtime.dll",
                            DiagnosticSeverity.Critical,
                            "ABI mismatch on RegisterTypeOptions.set_LogSuccess(bool) between Il2Cpp.dll and Il2CppInterop.Runtime.dll!",
                            $"Il2Cpp.dll calls '{callSite.ReturnType}' while Il2CppInterop.Runtime.dll exports '{runtimeLogSuccessSig.ReturnType}'. This throws MissingMethodException at startup."));
                    }
                    else
                    {
                        report.Checks.Add(new DiagnosticCheck(
                            "REGISTER_TYPE_OPTIONS_LOG_SUCCESS",
                            "Il2Cpp.dll <-> Il2CppInterop.Runtime.dll",
                            DiagnosticSeverity.Pass,
                            "RegisterTypeOptions.set_LogSuccess(bool) signature matches cleanly between Il2Cpp.dll and Il2CppInterop.Runtime.dll."));
                    }
                }
            }
        }

        // 3. HarmonyX, Il2CppInterop.HarmonySupport, JNISharp, MonoMod, Il2CppAssemblyGenerator, NativeHost
        CheckManagedAssembly(report, readBytes, mlPrefix + "net8/0Harmony.dll", "0Harmony.dll", (info) =>
        {
            bool hasReversePatch = info.TypeNames.Contains("HarmonyLib.PatchFunctions");
            if (!hasReversePatch)
                return (DiagnosticSeverity.Critical, "0Harmony.dll missing HarmonyLib.PatchFunctions.");
            return (DiagnosticSeverity.Pass, $"0Harmony.dll v{info.AssemblyVersion} OK.");
        });

        CheckManagedAssembly(report, readBytes, mlPrefix + "net8/Il2CppInterop.HarmonySupport.dll", "Il2CppInterop.HarmonySupport.dll",
            info => (DiagnosticSeverity.Pass, $"Il2CppInterop.HarmonySupport.dll v{info.AssemblyVersion} OK."));

        CheckManagedAssembly(report, readBytes, mlPrefix + "net8/Il2CppInterop.Common.dll", "Il2CppInterop.Common.dll",
            info => (DiagnosticSeverity.Pass, $"Il2CppInterop.Common.dll v{info.AssemblyVersion} OK."));

        CheckManagedAssembly(report, readBytes, mlPrefix + "net8/Il2CppInterop.Generator.dll", "Il2CppInterop.Generator.dll",
            info => (DiagnosticSeverity.Pass, $"Il2CppInterop.Generator.dll v{info.AssemblyVersion} OK."));

        CheckManagedAssembly(report, readBytes, mlPrefix + "net8/JNISharp.dll", "JNISharp.dll",
            info => (DiagnosticSeverity.Pass, $"JNISharp.dll v{info.AssemblyVersion} OK."));

        CheckManagedAssembly(report, readBytes, mlPrefix + "net8/MonoMod.Core.dll", "MonoMod.Core.dll",
            info => (DiagnosticSeverity.Pass, $"MonoMod.Core.dll v{info.AssemblyVersion} OK."));

        CheckManagedAssembly(report, readBytes, mlPrefix + "net8/MonoMod.RuntimeDetour.dll", "MonoMod.RuntimeDetour.dll",
            info => (DiagnosticSeverity.Pass, $"MonoMod.RuntimeDetour.dll v{info.AssemblyVersion} OK."));

        CheckManagedAssembly(report, readBytes, mlPrefix + "net8/MelonLoader.NativeHost.dll", "MelonLoader.NativeHost.dll",
            info => (DiagnosticSeverity.Pass, $"MelonLoader.NativeHost.dll v{info.AssemblyVersion} OK."));

        CheckManagedAssembly(report, readBytes, mlPrefix + "Dependencies/Il2CppAssemblyGenerator/Il2CppAssemblyGenerator.dll", "Il2CppAssemblyGenerator.dll",
            info => (DiagnosticSeverity.Pass, $"Il2CppAssemblyGenerator.dll v{info.AssemblyVersion} OK."));

        byte[]? runtimeConfig = readBytes(mlPrefix + "net8/MelonLoader.runtimeconfig.json");
        report.Checks.Add(new DiagnosticCheck(
            "RUNTIME_CONFIG",
            "MelonLoader.runtimeconfig.json",
            runtimeConfig != null ? DiagnosticSeverity.Pass : DiagnosticSeverity.Critical,
            runtimeConfig != null
                ? "MelonLoader.runtimeconfig.json present."
                : $"Missing {mlPrefix}net8/MelonLoader.runtimeconfig.json."));

        // 4. .NET 8 Android Runtime checks
        CheckArm64Elf(report, readBytes, dotnetPrefix + "host/fxr/8.0.6/libhostfxr.so", "dotnet/libhostfxr.so", required: true);
        CheckArm64Elf(report, readBytes, dotnetPrefix + "shared/Microsoft.NETCore.App/8.0.6/libcoreclr.so", "dotnet/libcoreclr.so", required: true);

        byte[]? sysRuntime = readBytes(dotnetPrefix + "shared/Microsoft.NETCore.App/8.0.6/System.Runtime.dll");
        report.Checks.Add(new DiagnosticCheck(
            "DOTNET_BCL",
            "dotnet/System.Runtime.dll",
            sysRuntime != null ? DiagnosticSeverity.Pass : DiagnosticSeverity.Critical,
            sysRuntime != null
                ? ".NET 8.0.6 BCL present."
                : "Missing .NET 8.0.6 System.Runtime.dll in dotnet runtime."));

        // 5. Native Android ARM64 binaries
        byte[]? bootstrapBytes = readBytes(nativePrefix + "libBootstrap.so");
        var bootstrapElf = CheckArm64Elf(report, readBytes, nativePrefix + "libBootstrap.so", "libBootstrap.so", required: true);
        if (bootstrapElf != null && bootstrapElf.IsArm64 && bootstrapBytes != null)
        {
            bool hasSslCertFix = ElfHeaderInspector.ContainsAscii(bootstrapBytes, "SSL_CERT_DIR");
            report.Checks.Add(new DiagnosticCheck(
                "BOOTSTRAP_SSL_FIX",
                "libBootstrap.so",
                hasSslCertFix ? DiagnosticSeverity.Pass : DiagnosticSeverity.Warning,
                hasSslCertFix
                    ? "libBootstrap.so includes Android SSL_CERT_DIR fix (needed for HTTPS Il2CppAssemblyGenerator/mods)."
                    : "libBootstrap.so is missing Android SSL_CERT_DIR environment fix (pre-1ddfecd build)."));
        }

        CheckArm64Elf(report, readBytes, nativePrefix + "libmain.so", "libmain.so", required: true);
        CheckArm64Elf(report, readBytes, nativePrefix + "libdobby.so", "libdobby.so", required: true);
        if (requireLibUnity)
            CheckArm64Elf(report, readBytes, nativePrefix + "libunity.so", "libunity.so", required: true);

        return report;
    }

    private static void CheckManagedAssembly(
        LemonDiagnosticReport report,
        Func<string, byte[]?> readBytes,
        string path,
        string component,
        Func<AssemblyInspectionResult, (DiagnosticSeverity Severity, string Message)> validator)
    {
        string id = component.Replace('.', '_').ToUpperInvariant();
        byte[]? bytes = readBytes(path);
        if (bytes == null)
        {
            report.Checks.Add(new DiagnosticCheck(id, component, DiagnosticSeverity.Critical, $"Missing {path}"));
            return;
        }

        var info = DotNetMetadataInspector.Inspect(bytes);
        if (!info.IsValidPe)
        {
            report.Checks.Add(new DiagnosticCheck(id, component, DiagnosticSeverity.Critical, $"{component} is invalid PE metadata.", info.Error));
            return;
        }

        var (sev, msg) = validator(info);
        report.Checks.Add(new DiagnosticCheck(id, component, sev, msg));
    }

    private static ElfInspectionResult? CheckArm64Elf(
        LemonDiagnosticReport report,
        Func<string, byte[]?> readBytes,
        string path,
        string component,
        bool required)
    {
        string id = component.Replace('/', '_').Replace('.', '_').ToUpperInvariant();
        byte[]? bytes = readBytes(path);
        if (bytes == null)
        {
            report.Checks.Add(new DiagnosticCheck(
                id,
                component,
                required ? DiagnosticSeverity.Critical : DiagnosticSeverity.Warning,
                $"Missing {path}"));
            return null;
        }

        var elf = ElfHeaderInspector.Inspect(bytes);
        if (!elf.IsValidElf || !elf.IsArm64)
        {
            report.Checks.Add(new DiagnosticCheck(
                id,
                component,
                DiagnosticSeverity.Critical,
                $"{component} is not a valid ARM64 (AArch64) ELF64 binary.",
                elf.Error ?? $"Detected architecture: {elf.ArchitectureName}"));
        }
        else
        {
            report.Checks.Add(new DiagnosticCheck(
                id,
                component,
                DiagnosticSeverity.Pass,
                $"{component} valid ELF64 AArch64 ({bytes.Length:N0} bytes, PT_LOAD align={elf.MaxLoadSegmentAlignment})."));
        }

        return elf;
    }
}
