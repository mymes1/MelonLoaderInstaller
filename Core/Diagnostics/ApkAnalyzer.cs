using AssetsTools.NET.Extra;
using MelonLoader.Installer.Core.Utilities.Signing;
using QuestPatcher.Axml;
using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Threading.Tasks;
using UnityVersion = AssetRipper.Primitives.UnityVersion;

namespace MelonLoader.Installer.Core.Diagnostics;

public sealed class ApkAnalysisReport
{
    public string PrimaryApkPath { get; init; } = "";
    public IReadOnlyList<string> AllApkPaths { get; init; } = [];
    public bool IsSplitBundle { get; init; }
    public string PackageName { get; init; } = "";
    public string VersionName { get; init; } = "";
    public string VersionCode { get; init; } = "";
    public int? MinSdkVersion { get; init; }
    public int? TargetSdkVersion { get; init; }
    public bool? Debuggable { get; init; }
    public bool? RequestLegacyExternalStorage { get; init; }
    public bool? ExtractNativeLibs { get; init; }
    public IReadOnlyList<string> Permissions { get; init; } = [];

    public UnityVersion UnityVersion { get; init; } = UnityVersion.MinVersion;
    public string UnityVersionString => UnityVersion == UnityVersion.MinVersion ? "Unknown" : UnityVersion.ToString();

    public IReadOnlyList<string> DetectedAbis { get; init; } = [];
    public bool HasArm64 => DetectedAbis.Contains("arm64-v8a");
    public bool HasLibUnity { get; init; }
    public bool HasLibIl2Cpp { get; init; }
    public bool HasLibMain { get; init; }
    public bool HasLibBootstrap { get; init; }
    public bool HasLibDobby { get; init; }
    public bool HasMelonLoaderAssets { get; init; }
    public bool HasDotnetAssets { get; init; }
    public string? LemonPatchDate { get; init; }
    public string? RepairManifestJson { get; init; }

    public ElfInspectionResult? LibUnityElf { get; init; }
    public ElfInspectionResult? LibIl2CppElf { get; init; }
    public ElfInspectionResult? LibBootstrapElf { get; init; }
    public ElfInspectionResult? LibMainElf { get; init; }

    public bool HasV1Signature { get; init; }
    public bool HasV2SignatureBlock { get; init; }
    public bool? OfficialUnityDepsAvailable { get; set; }
    public string? UnityDepsUrl { get; set; }

    public IReadOnlyList<string> Warnings { get; init; } = [];
    public IReadOnlyList<string> Errors { get; init; } = [];

    public bool IsSupportedUnityIl2CppArm64 => HasArm64 && HasLibUnity && HasLibIl2Cpp && UnityVersion != UnityVersion.MinVersion;
    public bool IsLemonPatched => HasMelonLoaderAssets || HasLibBootstrap;

    public string FormatReport()
    {
        var sb = new StringBuilder();
        sb.AppendLine("=== APK Analysis Report ===");
        sb.AppendLine($"Primary APK:        {PrimaryApkPath}");
        if (IsSplitBundle)
            sb.AppendLine($"Split APKs:         {AllApkPaths.Count} files");
        sb.AppendLine($"Package Name:       {(string.IsNullOrEmpty(PackageName) ? "(unknown)" : PackageName)}");
        sb.AppendLine($"App Version:        {VersionName} (code {VersionCode})");
        sb.AppendLine($"SDK Versions:       minSdk={MinSdkVersion?.ToString() ?? "?"}, targetSdk={TargetSdkVersion?.ToString() ?? "?"}");
        sb.AppendLine($"Unity Version:      {UnityVersionString}");
        sb.AppendLine($"ABIs Detected:      {(DetectedAbis.Count == 0 ? "(none)" : string.Join(", ", DetectedAbis))}");
        sb.AppendLine($"Backend / Runtime:  {(HasLibIl2Cpp ? "IL2CPP" : "Not IL2CPP / Unknown")} | libunity.so={(HasLibUnity ? "Present" : "Missing")}");
        if (LibUnityElf != null)
            sb.AppendLine($"libunity.so ELF:    {LibUnityElf.ArchitectureName}, max PT_LOAD align={LibUnityElf.MaxLoadSegmentAlignment}");
        if (LibIl2CppElf != null)
            sb.AppendLine($"libil2cpp.so ELF:   {LibIl2CppElf.ArchitectureName}, max PT_LOAD align={LibIl2CppElf.MaxLoadSegmentAlignment}");
        sb.AppendLine($"Manifest Flags:     debuggable={Debuggable?.ToString() ?? "unset"}, requestLegacyExternalStorage={RequestLegacyExternalStorage?.ToString() ?? "unset"}, extractNativeLibs={ExtractNativeLibs?.ToString() ?? "unset"}");
        sb.AppendLine($"APK Signatures:     V2={(HasV2SignatureBlock ? "Present" : "Absent")}, V1={(HasV1Signature ? "Present" : "Absent")}");
        sb.AppendLine($"LemonLoader State:  {(IsLemonPatched ? "Patched" : "Unpatched")} (assets/MelonLoader={HasMelonLoaderAssets}, assets/dotnet={HasDotnetAssets}, libBootstrap.so={HasLibBootstrap})");
        if (!string.IsNullOrWhiteSpace(LemonPatchDate))
            sb.AppendLine($"Lemon Patch Date:   {LemonPatchDate.Trim()}");
        if (OfficialUnityDepsAvailable.HasValue)
            sb.AppendLine($"Unity Deps Online:  {(OfficialUnityDepsAvailable.Value ? "Available" : "Unavailable")} ({UnityDepsUrl})");

        if (Errors.Count > 0)
        {
            sb.AppendLine("Errors:");
            foreach (var err in Errors)
                sb.AppendLine($"  [ERROR] {err}");
        }
        if (Warnings.Count > 0)
        {
            sb.AppendLine("Warnings:");
            foreach (var warn in Warnings)
                sb.AppendLine($"  [WARN]  {warn}");
        }

        return sb.ToString();
    }
}

public static class ApkAnalyzer
{
    public static ApkAnalysisReport Analyze(params string[] apkPaths)
    {
        if (apkPaths == null || apkPaths.Length == 0)
            throw new ArgumentException("At least one APK path is required.", nameof(apkPaths));

        var validPaths = apkPaths.Where(File.Exists).ToArray();
        if (validPaths.Length == 0)
            throw new FileNotFoundException("APK file not found.", apkPaths[0]);

        string primaryPath = validPaths.FirstOrDefault(p => Path.GetFileName(p).Equals("base.apk", StringComparison.OrdinalIgnoreCase))
            ?? validPaths[0];

        var warnings = new List<string>();
        var errors = new List<string>();

        string packageName = "";
        string versionName = "";
        string versionCode = "";
        int? minSdk = null;
        int? targetSdk = null;
        bool? debuggable = null;
        bool? requestLegacyExternalStorage = null;
        bool? extractNativeLibs = null;
        var permissions = new List<string>();

        var abis = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        bool hasLibUnity = false;
        bool hasLibIl2Cpp = false;
        bool hasLibMain = false;
        bool hasLibBootstrap = false;
        bool hasLibDobby = false;
        bool hasMelonAssets = false;
        bool hasDotnetAssets = false;
        string? lemonPatchDate = null;
        string? repairManifestJson = null;

        ElfInspectionResult? libUnityElf = null;
        ElfInspectionResult? libIl2CppElf = null;
        ElfInspectionResult? libBootstrapElf = null;
        ElfInspectionResult? libMainElf = null;

        bool hasV1 = false;
        bool hasV2 = false;
        UnityVersion unityVersion = UnityVersion.MinVersion;

        foreach (string apkPath in validPaths)
        {
            hasV2 = hasV2 || HasApkV2SignatureBlock(apkPath);

            using FileStream fs = new(apkPath, FileMode.Open, FileAccess.Read, FileShare.Read);
            using ZipArchive archive = new(fs, ZipArchiveMode.Read);

            if (apkPath == primaryPath)
            {
                var manifestEntry = archive.GetEntry("AndroidManifest.xml");
                if (manifestEntry != null)
                {
                    try
                    {
                        using MemoryStream ms = new();
                        using (var es = manifestEntry.Open())
                            es.CopyTo(ms);
                        ms.Position = 0;

                        AxmlElement manifest = AxmlLoader.LoadDocument(ms);
                        packageName = GetAttrString(manifest, "package") ?? "";
                        versionName = GetAttrString(manifest, "versionName") ?? "";
                        versionCode = GetAttrString(manifest, "versionCode") ?? "";

                        var usesSdk = manifest.Children.FirstOrDefault(c => c.Name == "uses-sdk");
                        if (usesSdk != null)
                        {
                            minSdk = GetAttrInt(usesSdk, "minSdkVersion");
                            targetSdk = GetAttrInt(usesSdk, "targetSdkVersion");
                        }

                        foreach (var perm in manifest.Children.Where(c => c.Name == "uses-permission"))
                        {
                            string? p = GetAttrString(perm, "name");
                            if (!string.IsNullOrEmpty(p))
                                permissions.Add(p);
                        }

                        var appElem = manifest.Children.FirstOrDefault(c => c.Name == "application");
                        if (appElem != null)
                        {
                            debuggable = GetAttrBool(appElem, "debuggable");
                            requestLegacyExternalStorage = GetAttrBool(appElem, "requestLegacyExternalStorage");
                            extractNativeLibs = GetAttrBool(appElem, "extractNativeLibs");
                        }
                    }
                    catch (Exception ex)
                    {
                        warnings.Add($"Failed to parse AndroidManifest.xml: {ex.Message}");
                    }
                }
                else
                {
                    errors.Add("Missing AndroidManifest.xml in base APK.");
                }

                if (unityVersion == UnityVersion.MinVersion)
                    unityVersion = DetectUnityVersionFromArchive(archive);
            }

            foreach (var entry in archive.Entries)
            {
                string full = entry.FullName.Replace('\\', '/');

                if (full.StartsWith("META-INF/", StringComparison.OrdinalIgnoreCase) &&
                    (full.EndsWith(".SF", StringComparison.OrdinalIgnoreCase) ||
                     full.EndsWith(".RSA", StringComparison.OrdinalIgnoreCase) ||
                     full.EndsWith(".EC", StringComparison.OrdinalIgnoreCase)))
                {
                    hasV1 = true;
                }

                if (full.StartsWith("assets/MelonLoader/", StringComparison.OrdinalIgnoreCase))
                    hasMelonAssets = true;
                if (full.StartsWith("assets/dotnet/", StringComparison.OrdinalIgnoreCase))
                    hasDotnetAssets = true;

                if (full.Equals("assets/lemon_patch_date.txt", StringComparison.OrdinalIgnoreCase))
                {
                    using var sr = new StreamReader(entry.Open(), Encoding.UTF8);
                    lemonPatchDate = sr.ReadToEnd();
                }

                if (full.Equals("assets/MelonLoader/repair-manifest.json", StringComparison.OrdinalIgnoreCase))
                {
                    using var sr = new StreamReader(entry.Open(), Encoding.UTF8);
                    repairManifestJson = sr.ReadToEnd();
                }

                if (full.StartsWith("lib/", StringComparison.OrdinalIgnoreCase))
                {
                    var parts = full.Split('/');
                    if (parts.Length >= 3 && !string.IsNullOrWhiteSpace(parts[1]))
                        abis.Add(parts[1]);

                    if (full.Equals("lib/arm64-v8a/libunity.so", StringComparison.OrdinalIgnoreCase))
                    {
                        hasLibUnity = true;
                        using var s = entry.Open();
                        libUnityElf = ElfHeaderInspector.InspectStream(s);
                    }
                    else if (full.Equals("lib/arm64-v8a/libil2cpp.so", StringComparison.OrdinalIgnoreCase))
                    {
                        hasLibIl2Cpp = true;
                        using var s = entry.Open();
                        libIl2CppElf = ElfHeaderInspector.InspectStream(s);
                    }
                    else if (full.Equals("lib/arm64-v8a/libmain.so", StringComparison.OrdinalIgnoreCase))
                    {
                        hasLibMain = true;
                        using var s = entry.Open();
                        libMainElf = ElfHeaderInspector.InspectStream(s);
                    }
                    else if (full.Equals("lib/arm64-v8a/libBootstrap.so", StringComparison.OrdinalIgnoreCase))
                    {
                        hasLibBootstrap = true;
                        using var s = entry.Open();
                        libBootstrapElf = ElfHeaderInspector.InspectStream(s);
                    }
                    else if (full.Equals("lib/arm64-v8a/libdobby.so", StringComparison.OrdinalIgnoreCase))
                    {
                        hasLibDobby = true;
                    }
                }
            }
        }

        if (!abis.Contains("arm64-v8a"))
            errors.Add("APK does not contain arm64-v8a native libraries.");
        if (!hasLibUnity)
            errors.Add("Missing lib/arm64-v8a/libunity.so (not a Unity ARM64 APK).");
        if (!hasLibIl2Cpp)
            errors.Add("Missing lib/arm64-v8a/libil2cpp.so (not an IL2CPP ARM64 APK).");
        if (unityVersion == UnityVersion.MinVersion)
            errors.Add("Could not determine Unity engine version from globalgamemanagers or data.unity3d.");

        if (libUnityElf is { IsValidElf: true, IsArm64: false })
            errors.Add($"lib/arm64-v8a/libunity.so is not AArch64 ELF64 (found {libUnityElf.ArchitectureName}).");
        if (libIl2CppElf is { IsValidElf: true, IsArm64: false })
            errors.Add($"lib/arm64-v8a/libil2cpp.so is not AArch64 ELF64 (found {libIl2CppElf.ArchitectureName}).");

        if ((hasMelonAssets || hasLibBootstrap) && extractNativeLibs == false)
            warnings.Add("android:extractNativeLibs is false while patched; Android 15 requires extractNativeLibs=true when .so files are compressed.");

        if (targetSdk >= 35 && hasMelonAssets && !permissions.Contains("android.permission.MANAGE_EXTERNAL_STORAGE"))
            warnings.Add("Target SDK >= 35 without MANAGE_EXTERNAL_STORAGE in manifest.");

        if (!hasV2 && !hasV1)
            warnings.Add("APK appears unsigned (neither v2 signing block nor v1 META-INF signature found).");
        else if (!hasV2 && targetSdk >= 30)
            warnings.Add("APK lacks v2 signature block; Android 11–15 requires APK Signature Scheme v2+.");

        return new ApkAnalysisReport
        {
            PrimaryApkPath = primaryPath,
            AllApkPaths = validPaths,
            IsSplitBundle = validPaths.Length > 1,
            PackageName = packageName,
            VersionName = versionName,
            VersionCode = versionCode,
            MinSdkVersion = minSdk,
            TargetSdkVersion = targetSdk,
            Debuggable = debuggable,
            RequestLegacyExternalStorage = requestLegacyExternalStorage,
            ExtractNativeLibs = extractNativeLibs,
            Permissions = permissions,
            UnityVersion = unityVersion,
            DetectedAbis = abis.OrderBy(a => a).ToArray(),
            HasLibUnity = hasLibUnity,
            HasLibIl2Cpp = hasLibIl2Cpp,
            HasLibMain = hasLibMain,
            HasLibBootstrap = hasLibBootstrap,
            HasLibDobby = hasLibDobby,
            HasMelonLoaderAssets = hasMelonAssets,
            HasDotnetAssets = hasDotnetAssets,
            LemonPatchDate = lemonPatchDate,
            RepairManifestJson = repairManifestJson,
            LibUnityElf = libUnityElf,
            LibIl2CppElf = libIl2CppElf,
            LibBootstrapElf = libBootstrapElf,
            LibMainElf = libMainElf,
            HasV1Signature = hasV1,
            HasV2SignatureBlock = hasV2,
            Warnings = warnings,
            Errors = errors
        };
    }

    public static async Task ProbeOfficialUnityDependenciesAsync(ApkAnalysisReport report)
    {
        if (report.UnityVersion == UnityVersion.MinVersion)
        {
            report.OfficialUnityDepsAvailable = false;
            return;
        }

        string ver = report.UnityVersion.ToStringWithoutType();
        string url = report.UnityVersion.Type == AssetRipper.Primitives.UnityVersionType.China
            ? $"https://github.com/LemonLoader/MelonLoader.UnityDependencies.China/releases/download/{ver}/libunity.so.arm64-v8a"
            : $"https://github.com/LavaGang/MelonLoader.UnityDependencies/releases/download/{ver}/libunity.so.arm64-v8a";
        report.UnityDepsUrl = url;

        try
        {
            using HttpClient client = new();
            client.DefaultRequestHeaders.UserAgent.ParseAdd("MelonLoaderInstaller/1.0");
            client.Timeout = TimeSpan.FromSeconds(15);

            string apiUrl = report.UnityVersion.Type == AssetRipper.Primitives.UnityVersionType.China
                ? $"https://api.github.com/repos/LemonLoader/MelonLoader.UnityDependencies.China/releases/tags/{ver}"
                : $"https://api.github.com/repos/LavaGang/MelonLoader.UnityDependencies/releases/tags/{ver}";

            using var resp = await client.GetAsync(apiUrl);
            if (!resp.IsSuccessStatusCode)
            {
                report.OfficialUnityDepsAvailable = false;
                return;
            }

            string json = await resp.Content.ReadAsStringAsync();
            report.OfficialUnityDepsAvailable = json.Contains("libunity.so.arm64-v8a", StringComparison.OrdinalIgnoreCase);
        }
        catch
        {
            report.OfficialUnityDepsAvailable = null;
        }
    }

    public static UnityVersion DetectUnityVersionFromArchive(ZipArchive archive)
    {
        AssetsManager uAssetsManager = new();

        try
        {
            var ggm = archive.GetEntry("assets/bin/Data/globalgamemanagers");
            if (ggm != null)
            {
                using Stream s = ggm.Open();
                using MemoryStream ms = new();
                s.CopyTo(ms);
                ms.Position = 0;
                AssetsFileInstance instance = uAssetsManager.LoadAssetsFile(ms, "/bin/Data/globalgamemanagers", true);
                return UnityVersion.Parse(instance.file.Metadata.UnityVersion);
            }
        }
        catch { }

        try
        {
            var dataUnity = archive.GetEntry("assets/bin/Data/data.unity3d");
            if (dataUnity != null)
            {
                using Stream s = dataUnity.Open();
                using MemoryStream ms = new();
                s.CopyTo(ms);
                ms.Position = 0;
                BundleFileInstance bundle = uAssetsManager.LoadBundleFile(ms, "/bin/Data/data.unity3d");
                AssetsFileInstance instance = uAssetsManager.LoadAssetsFileFromBundle(bundle, "globalgamemanagers");
                return UnityVersion.Parse(instance.file.Metadata.UnityVersion);
            }
        }
        catch { }

        return UnityVersion.MinVersion;
    }

    public static bool HasApkV2SignatureBlock(string apkPath)
    {
        try
        {
            using FileStream fs = new(apkPath, FileMode.Open, FileAccess.Read, FileShare.Read);
            using FileMemory memory = new(fs);
            if (memory.Length() < 22)
                return false;

            long pos = memory.Length() - 22;
            long minSearch = Math.Max(0, memory.Length() - 65557);
            while (pos >= minSearch)
            {
                memory.Position = pos;
                if (memory.ReadInt() == EndOfCentralDirectory.SIGNATURE)
                    break;
                pos--;
            }
            if (pos < minSearch)
                return false;

            memory.Position = pos;
            EndOfCentralDirectory eocd = new(memory);
            long cdOffset = (uint)eocd.OffsetOfCD;
            if (cdOffset < 24 || cdOffset > fs.Length)
                return false;

            memory.Position = cdOffset - 24;
            ulong blockSizeFooter = memory.ReadULong();
            string magic = memory.ReadString(16);
            return magic == "APK Sig Block 42" && blockSizeFooter >= 24;
        }
        catch
        {
            return false;
        }
    }

    private static string? GetAttrString(AxmlElement element, string name)
        => element.Attributes.FirstOrDefault(a => a.Name == name)?.Value?.ToString();

    private static int? GetAttrInt(AxmlElement element, string name)
    {
        var attr = element.Attributes.FirstOrDefault(a => a.Name == name);
        if (attr?.Value == null)
            return null;
        return attr.Value is int i ? i : int.TryParse(attr.Value.ToString(), out int parsed) ? parsed : null;
    }

    private static bool? GetAttrBool(AxmlElement element, string name)
    {
        var attr = element.Attributes.FirstOrDefault(a => a.Name == name);
        if (attr?.Value == null)
            return null;
        return attr.Value is bool b ? b : bool.TryParse(attr.Value.ToString(), out bool parsed) ? parsed : null;
    }
}
