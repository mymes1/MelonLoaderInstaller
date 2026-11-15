using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;

namespace MelonLoader.Installer.Core.Repair;

public sealed record ExtractedSplitBundle(
    string ExtractDirectory,
    string BaseApkPath,
    string? Arm64SplitApkPath,
    string[] ExtraSplitApkPaths)
{
    public string[] AllApkPaths => new[] { BaseApkPath }
        .Concat(Arm64SplitApkPath != null ? [Arm64SplitApkPath] : Array.Empty<string>())
        .Concat(ExtraSplitApkPaths)
        .ToArray();
}

public static class SplitApkBundleExtractor
{
    public static bool IsBundleArchive(string path)
    {
        string ext = Path.GetExtension(path);
        return ext.Equals(".apks", StringComparison.OrdinalIgnoreCase)
            || ext.Equals(".xapk", StringComparison.OrdinalIgnoreCase)
            || ext.Equals(".zip", StringComparison.OrdinalIgnoreCase);
    }

    public static ExtractedSplitBundle Extract(string bundlePath, string destinationDirectory)
    {
        if (!File.Exists(bundlePath))
            throw new FileNotFoundException("Split bundle archive not found.", bundlePath);

        if (Directory.Exists(destinationDirectory))
            Directory.Delete(destinationDirectory, true);
        Directory.CreateDirectory(destinationDirectory);

        using FileStream fs = new(bundlePath, FileMode.Open, FileAccess.Read, FileShare.Read);
        using ZipArchive archive = new(fs, ZipArchiveMode.Read);

        var extractedApks = new List<string>();
        foreach (var entry in archive.Entries)
        {
            if (!entry.FullName.EndsWith(".apk", StringComparison.OrdinalIgnoreCase))
                continue;

            string fileName = Path.GetFileName(entry.FullName);
            if (string.IsNullOrEmpty(fileName))
                continue;

            string outPath = Path.Combine(destinationDirectory, fileName);
            entry.ExtractToFile(outPath, overwrite: true);
            extractedApks.Add(outPath);
        }

        if (extractedApks.Count == 0)
            throw new InvalidOperationException("Archive does not contain any .apk files.");

        return ResolveApkLayout(extractedApks, destinationDirectory);
    }

    public static ExtractedSplitBundle ResolveApkLayout(IEnumerable<string> apkPaths, string? extractDirectory = null)
    {
        var list = apkPaths.Where(p => !string.IsNullOrWhiteSpace(p)).ToList();
        if (list.Count == 0)
            throw new ArgumentException("At least one APK path is required.", nameof(apkPaths));

        string? baseApk = list.FirstOrDefault(p =>
            Path.GetFileName(p).Equals("base.apk", StringComparison.OrdinalIgnoreCase))
            ?? list.FirstOrDefault(HasUnityDataEntries)
            ?? list.FirstOrDefault(p => !Path.GetFileName(p).StartsWith("split_", StringComparison.OrdinalIgnoreCase))
            ?? list[0];

        string? arm64Split = list.FirstOrDefault(p =>
            !string.Equals(p, baseApk, StringComparison.OrdinalIgnoreCase) &&
            (Path.GetFileName(p).Contains("arm64", StringComparison.OrdinalIgnoreCase) || HasArm64Libs(p)));

        string[] extraSplits = list
            .Where(p => !string.Equals(p, baseApk, StringComparison.OrdinalIgnoreCase)
                     && !string.Equals(p, arm64Split, StringComparison.OrdinalIgnoreCase))
            .ToArray();

        string dir = extractDirectory ?? (Path.GetDirectoryName(baseApk) ?? "");
        return new ExtractedSplitBundle(dir, baseApk, arm64Split, extraSplits);
    }

    private static bool HasUnityDataEntries(string apkPath)
    {
        try
        {
            if (!File.Exists(apkPath))
                return false;
            using var s = File.OpenRead(apkPath);
            using var z = new ZipArchive(s, ZipArchiveMode.Read);
            return z.GetEntry("assets/bin/Data/globalgamemanagers") != null
                || z.GetEntry("assets/bin/Data/data.unity3d") != null;
        }
        catch
        {
            return false;
        }
    }

    private static bool HasArm64Libs(string apkPath)
    {
        try
        {
            if (!File.Exists(apkPath))
                return false;
            using var fs = File.OpenRead(apkPath);
            using var z = new ZipArchive(fs, ZipArchiveMode.Read);
            return z.Entries.Any(e => e.FullName.Replace('\\', '/').StartsWith("lib/arm64-v8a/", StringComparison.OrdinalIgnoreCase));
        }
        catch
        {
            return false;
        }
    }
}
