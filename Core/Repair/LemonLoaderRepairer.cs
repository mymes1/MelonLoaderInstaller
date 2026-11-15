using MelonLoader.Installer.Core.Diagnostics;
using MelonLoader.Installer.Core.PatchSteps;
using MelonLoader.Installer.Core.Utilities.Signing;
using QuestPatcher.Axml;
using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using System.Xml;

namespace MelonLoader.Installer.Core.Repair;

public static class LemonLoaderRepairer
{
    public const string RepairedBundleFileName = "melon_data_repaired.zip";
    private const string OfficialInstallerReleasesApi = "https://api.github.com/repos/mymes1/MelonLoaderInstaller/releases/latest";

    public static string? FindLocalRepairedBundle()
    {
        string appDir = AppContext.BaseDirectory;
        string appDataCache = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
            "MelonLoader.Installer.App",
            "Cache",
            RepairedBundleFileName);

        string[] candidates =
        [
            Path.Combine(appDir, "Resources", "repaired", RepairedBundleFileName),
            Path.Combine(appDir, "Resources", RepairedBundleFileName),
            Path.Combine(appDir, RepairedBundleFileName),
            appDataCache
        ];

        foreach (string candidate in candidates)
        {
            if (!File.Exists(candidate))
                continue;

            try
            {
                var report = LemonDataDiagnostics.Inspect(candidate);
                if (report.IsHealthy)
                    return candidate;
            }
            catch { }
        }

        return null;
    }

    public static async Task<string> ResolveOrDownloadRepairedBundleAsync(IPatchLogger logger)
    {
        string? local = FindLocalRepairedBundle();
        if (local != null)
        {
            logger.Log($"Using verified repaired LemonLoader bundle: [ {local} ]");
            return local;
        }

        string cacheDir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
            "MelonLoader.Installer.App",
            "Cache");
        Directory.CreateDirectory(cacheDir);
        string cacheTarget = Path.Combine(cacheDir, RepairedBundleFileName);

        logger.Log("Checking official GitHub releases for verified repaired LemonLoader bundle...");
        using HttpClient client = new();
        client.DefaultRequestHeaders.UserAgent.ParseAdd("MelonLoaderInstaller/1.0");
        client.Timeout = TimeSpan.FromMinutes(5);

        using var response = await client.GetAsync(OfficialInstallerReleasesApi);
        if (!response.IsSuccessStatusCode)
        {
            throw new InvalidOperationException(
                $"No local {RepairedBundleFileName} found and GitHub release lookup returned {(int)response.StatusCode}. " +
                $"Place {RepairedBundleFileName} in Resources/repaired/ next to the application or build it via GitHub Actions.");
        }

        string json = await response.Content.ReadAsStringAsync();
        using var doc = JsonDocument.Parse(json);
        if (!doc.RootElement.TryGetProperty("assets", out var assets))
            throw new InvalidOperationException("GitHub release metadata had no assets.");

        string? assetUrl = null;
        foreach (var asset in assets.EnumerateArray())
        {
            string? name = asset.TryGetProperty("name", out var n) ? n.GetString() : null;
            if (name != null && name.Equals(RepairedBundleFileName, StringComparison.OrdinalIgnoreCase))
            {
                assetUrl = asset.TryGetProperty("browser_download_url", out var u) ? u.GetString() : null;
                break;
            }
        }

        if (string.IsNullOrEmpty(assetUrl))
            throw new InvalidOperationException($"GitHub release does not contain {RepairedBundleFileName}.");

        logger.Log($"Downloading [ {assetUrl} ]");
        byte[] bytes = await client.GetByteArrayAsync(assetUrl);
        await File.WriteAllBytesAsync(cacheTarget, bytes);

        var report = LemonDataDiagnostics.Inspect(cacheTarget);
        if (!report.IsHealthy)
            throw new InvalidOperationException("Downloaded repaired LemonLoader bundle failed verification:\n" + report.FormatReport());

        logger.Log($"Verified repaired LemonLoader bundle saved to [ {cacheTarget} ]");
        return cacheTarget;
    }

    public static LemonDiagnosticReport RepairMelonDataZip(
        string inputZipPath,
        string outputZipPath,
        string repairedBundlePath,
        IPatchLogger logger)
    {
        if (!File.Exists(inputZipPath))
            throw new FileNotFoundException("Input melon_data.zip not found.", inputZipPath);
        if (!File.Exists(repairedBundlePath))
            throw new FileNotFoundException("Repaired LemonLoader bundle not found.", repairedBundlePath);

        logger.Log($"Diagnosing input LemonLoader archive [ {inputZipPath} ]...");
        var before = LemonDataDiagnostics.Inspect(inputZipPath);
        logger.Log(before.FormatReport());

        string tempDir = Path.Combine(Path.GetTempPath(), "lemon_repair_zip_" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(tempDir);

        try
        {
            using (var inputFs = File.OpenRead(inputZipPath))
            using (var inputZip = new ZipArchive(inputFs, ZipArchiveMode.Read))
            {
                inputZip.ExtractToDirectory(tempDir, overwriteFiles: true);
            }

            string repairedExtract = Path.Combine(tempDir, "_repaired_src");
            Directory.CreateDirectory(repairedExtract);
            using (var repFs = File.OpenRead(repairedBundlePath))
            using (var repZip = new ZipArchive(repFs, ZipArchiveMode.Read))
            {
                repZip.ExtractToDirectory(repairedExtract, overwriteFiles: true);
            }

            // Replace MelonLoader managed assemblies and native ARM64 binaries cleanly
            string outMelonDir = Path.Combine(tempDir, "MelonLoader");
            if (Directory.Exists(outMelonDir))
                Directory.Delete(outMelonDir, true);
            CopyDirectory(Path.Combine(repairedExtract, "MelonLoader"), outMelonDir);

            string outNativeDir = Path.Combine(tempDir, "native");
            if (Directory.Exists(outNativeDir))
                Directory.Delete(outNativeDir, true);
            CopyDirectory(Path.Combine(repairedExtract, "native"), outNativeDir);

            // Ensure dotnet runtime is healthy; refresh from repaired bundle if missing or broken
            string outDotnetDir = Path.Combine(tempDir, "dotnet");
            string srcDotnetDir = Path.Combine(repairedExtract, "dotnet");
            if (Directory.Exists(srcDotnetDir))
            {
                if (Directory.Exists(outDotnetDir))
                    Directory.Delete(outDotnetDir, true);
                CopyDirectory(srcDotnetDir, outDotnetDir);
            }

            Directory.Delete(repairedExtract, true);

            string? outParent = Path.GetDirectoryName(Path.GetFullPath(outputZipPath));
            if (!string.IsNullOrEmpty(outParent))
                Directory.CreateDirectory(outParent);
            if (File.Exists(outputZipPath))
                File.Delete(outputZipPath);

            ZipFile.CreateFromDirectory(tempDir, outputZipPath, CompressionLevel.Optimal, includeBaseDirectory: false);
            logger.Log($"Created repaired archive [ {outputZipPath} ]");

            var after = LemonDataDiagnostics.Inspect(outputZipPath);
            logger.Log(after.FormatReport());
            if (!after.IsHealthy)
                throw new InvalidOperationException("Repaired melon_data.zip still has critical diagnostic failures.");

            return after;
        }
        finally
        {
            try { Directory.Delete(tempDir, true); } catch { }
        }
    }

    public static LemonDiagnosticReport RepairPatchedApk(
        string inputApkPath,
        string outputApkPath,
        string repairedBundlePath,
        string? localUnityDepsPath,
        IPatchLogger logger,
        bool replaceLibUnity = false,
        LemonPatchStage stage = LemonPatchStage.Full)
    {
        if (!File.Exists(inputApkPath))
            throw new FileNotFoundException("Input APK not found.", inputApkPath);
        if (!File.Exists(repairedBundlePath))
            throw new FileNotFoundException("Repaired LemonLoader bundle not found.", repairedBundlePath);

        logger.Log($"Analyzing APK before repair [ {inputApkPath} ]...");
        var apkAnalysis = ApkAnalyzer.Analyze(inputApkPath);
        logger.Log(apkAnalysis.FormatReport());

        if (!apkAnalysis.IsSupportedUnityIl2CppArm64)
            throw new InvalidOperationException("APK is not a supported Unity ARM64 IL2CPP application.");

        string tempWork = Path.Combine(Path.GetTempPath(), "lemon_repair_apk_" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(tempWork);

        try
        {
            string bundleExtract = Path.Combine(tempWork, "lemon_data");
            Directory.CreateDirectory(bundleExtract);
            using (var repFs = File.OpenRead(repairedBundlePath))
            using (var repZip = new ZipArchive(repFs, ZipArchiveMode.Read))
                repZip.ExtractToDirectory(bundleExtract, overwriteFiles: true);

            string unityDir = Path.Combine(tempWork, "unity");
            string unityArm64Dir = Path.Combine(unityDir, "arm64-v8a");
            Directory.CreateDirectory(unityArm64Dir);

            string workApk = Path.Combine(tempWork, "repaired.apk");
            File.Copy(inputApkPath, workApk, overwrite: true);

            // Preserve original libunity.so unless replaceLibUnity or localUnityDepsPath is explicitly provided
            var patchArgs = new PatchArguments(
                workApk,
                "",
                [],
                tempWork,
                tempWork,
                repairedBundlePath,
                localUnityDepsPath ?? "",
                apkAnalysis.UnityVersion,
                apkAnalysis.PackageName,
                false,
                replaceLibUnity: replaceLibUnity,
                patchStage: stage);
            var patcher = new Patcher(patchArgs, logger);

            if (!string.IsNullOrEmpty(localUnityDepsPath) && File.Exists(localUnityDepsPath))
            {
                if (!new ExtractUnityLibs().Run(patcher))
                    throw new InvalidOperationException("Failed to extract local Unity dependencies.");
            }
            else if (replaceLibUnity)
            {
                if (!new DownloadUnityDeps().Run(patcher))
                    throw new InvalidOperationException("Failed to obtain unstripped libunity.so for Unity " + apkAnalysis.UnityVersionString);
            }
            else
            {
                logger.Log("Preserving original APK lib/arm64-v8a/libunity.so during repair.");
            }

            // Update AndroidManifest.xml (permissions, debuggable, requestLegacyExternalStorage, extractNativeLibs=true)
            PatchManifestForWorkApk(workApk, logger);

            // Repack LemonLoader assets & ARM64 native libs while preserving original APK entries
            using (FileStream zipStream = new(workApk, FileMode.Open, FileAccess.ReadWrite))
            using (ZipArchive archive = new(zipStream, ZipArchiveMode.Update))
            {
                DeleteEntriesWithPrefix(archive, "assets/MelonLoader/");
                DeleteEntriesWithPrefix(archive, "assets/dotnet/");

                CopyDirectoryToArchive(archive, Path.Combine(bundleExtract, "MelonLoader"), "assets/MelonLoader");
                CopyDirectoryToArchive(archive, Path.Combine(bundleExtract, "dotnet"), "assets/dotnet");
                WritePatchDate(archive);
                WriteStageConfig(archive, stage);

                string nativeSrcDir = Path.Combine(bundleExtract, "native");
                if (!File.Exists(Path.Combine(nativeSrcDir, "libBootstrap.so")))
                {
                    archive.GetEntry("lib/arm64-v8a/libBootstrap.so")?.Delete();
                    archive.GetEntry("lib/arm64-v8a/libdobby.so")?.Delete();
                }
                CopyDirectoryToArchive(archive, nativeSrcDir, "lib/arm64-v8a", "*.so");
                if (Directory.GetFiles(unityArm64Dir, "*.so", SearchOption.AllDirectories).Length > 0)
                    CopyDirectoryToArchive(archive, unityArm64Dir, "lib/arm64-v8a", "*.so");
            }

            // Generate certificate, align, and sign V2
            var certStep = new GenerateCertificate();
            if (!certStep.Run(patcher))
                throw new InvalidOperationException("Certificate generation failed.");

            var signer = new APKSigner(patcher.Info.PemData, logger);
            signer.Sign(workApk);

            string? outParent = Path.GetDirectoryName(Path.GetFullPath(outputApkPath));
            if (!string.IsNullOrEmpty(outParent))
                Directory.CreateDirectory(outParent);
            File.Copy(workApk, outputApkPath, overwrite: true);

            logger.Log($"Repaired APK saved to [ {outputApkPath} ]");
            var finalReport = LemonDataDiagnostics.Inspect(outputApkPath);
            logger.Log(finalReport.FormatReport());
            return finalReport;
        }
        finally
        {
            try { Directory.Delete(tempWork, true); } catch { }
        }
    }

    private static void PatchManifestForWorkApk(string apkPath, IPatchLogger logger)
    {
        Uri androidNs = new("http://schemas.android.com/apk/res/android");
        const int nameAttrId = 16842755;
        const int debuggableAttrId = 16842767;
        const int legacyStorageAttrId = 16844291;
        const int extractNativeLibsAttrId = 16844010;

        string[] requiredPerms =
        [
            "android.permission.READ_EXTERNAL_STORAGE",
            "android.permission.WRITE_EXTERNAL_STORAGE",
            "android.permission.MANAGE_EXTERNAL_STORAGE",
            "android.permission.RECORD_AUDIO",
            "android.permission.INTERNET"
        ];

        using FileStream zipStream = new(apkPath, FileMode.Open, FileAccess.ReadWrite);
        using ZipArchive archive = new(zipStream, ZipArchiveMode.Update);
        var manifestEntry = archive.Entries.First(e => e.FullName == "AndroidManifest.xml");
        using MemoryStream ms = new();
        using (var s = manifestEntry.Open())
            s.CopyTo(ms);
        ms.Position = 0;

        AxmlElement manifest = AxmlLoader.LoadDocument(ms);

        var existingPerms = new HashSet<string>(StringComparer.Ordinal);
        foreach (var child in manifest.Children.Where(c => c.Name == "uses-permission"))
        {
            var attr = child.Attributes.FirstOrDefault(a => a.Namespace == androidNs && a.Name == "name");
            if (attr?.Value is string p)
                existingPerms.Add(p);
        }

        foreach (string perm in requiredPerms)
        {
            if (existingPerms.Contains(perm))
                continue;
            logger.Log($"Adding permission {perm}");
            var elem = new AxmlElement("uses-permission");
            elem.Attributes.Add(new("name", androidNs, nameAttrId, perm));
            manifest.Children.Add(elem);
        }

        var appElem = manifest.Children.FirstOrDefault(e => e.Name == "application");
        if (appElem != null)
        {
            if (!appElem.Attributes.Any(a => a.Name == "debuggable"))
                appElem.Attributes.Add(new("debuggable", androidNs, debuggableAttrId, true));

            if (!appElem.Attributes.Any(a => a.Name == "requestLegacyExternalStorage"))
                appElem.Attributes.Add(new("requestLegacyExternalStorage", androidNs, legacyStorageAttrId, true));

            var extract = appElem.Attributes.FirstOrDefault(a => a.Name == "extractNativeLibs");
            if (extract != null)
                extract.Value = true;
            else
                appElem.Attributes.Add(new("extractNativeLibs", androidNs, extractNativeLibsAttrId, true));
        }

        using MemoryStream outMs = new();
        AxmlSaver.SaveDocument(outMs, manifest);
        outMs.Position = 0;

        using var writeStream = manifestEntry.Open();
        writeStream.Position = 0;
        writeStream.SetLength(0);
        outMs.CopyTo(writeStream);
    }

    private static void DeleteEntriesWithPrefix(ZipArchive archive, string prefix)
    {
        var matches = archive.Entries
            .Where(e => e.FullName.Replace('\\', '/').StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
            .ToArray();
        foreach (var entry in matches)
            entry.Delete();
    }

    private static void CopyDirectoryToArchive(ZipArchive archive, string sourceDir, string destPrefix, string matcher = "*.*")
    {
        if (!Directory.Exists(sourceDir))
            return;

        foreach (string file in Directory.GetFiles(sourceDir, matcher, SearchOption.AllDirectories))
        {
            string entryPath = Path.Combine(destPrefix, Path.GetRelativePath(sourceDir, file)).Replace('\\', '/');
            var existing = archive.Entries.FirstOrDefault(e =>
                e.FullName.Replace('\\', '/').Equals(entryPath, StringComparison.OrdinalIgnoreCase));
            existing?.Delete();

            CompressionLevel level = file.EndsWith(".so", StringComparison.OrdinalIgnoreCase)
                ? CompressionLevel.NoCompression
                : CompressionLevel.Optimal;
            var entry = archive.CreateEntry(entryPath, level);
            using var src = File.OpenRead(file);
            using var dst = entry.Open();
            src.CopyTo(dst);
        }
    }

    private static void WriteStageConfig(ZipArchive archive, LemonPatchStage stage)
    {
        const string entryPath = "assets/lemon_stage.txt";
        archive.GetEntry(entryPath)?.Delete();
        var entry = archive.CreateEntry(entryPath, CompressionLevel.Optimal);
        using var s = entry.Open();
        s.Write(Encoding.UTF8.GetBytes(stage.ToAssetToken()));
    }

    private static void WritePatchDate(ZipArchive archive)
    {
        const string entryPath = "assets/lemon_patch_date.txt";
        archive.GetEntry(entryPath)?.Delete();
        string rfc3339 = XmlConvert.ToString(DateTime.UtcNow, XmlDateTimeSerializationMode.Utc);
        var entry = archive.CreateEntry(entryPath, CompressionLevel.Optimal);
        using var s = entry.Open();
        s.Write(Encoding.UTF8.GetBytes(rfc3339));
    }

    private static void CopyDirectory(string sourceDir, string destDir)
    {
        Directory.CreateDirectory(destDir);
        foreach (string file in Directory.GetFiles(sourceDir, "*", SearchOption.AllDirectories))
        {
            string rel = Path.GetRelativePath(sourceDir, file);
            string target = Path.Combine(destDir, rel);
            string? targetDir = Path.GetDirectoryName(target);
            if (!string.IsNullOrEmpty(targetDir))
                Directory.CreateDirectory(targetDir);
            File.Copy(file, target, overwrite: true);
        }
    }
}
