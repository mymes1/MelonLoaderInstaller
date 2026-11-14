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

        string? baseApk = extractedApks.FirstOrDefault(p =>
            Path.GetFileName(p).Equals("base.apk", StringComparison.OrdinalIgnoreCase))
            ?? extractedApks.FirstOrDefault(p =>
            {
                using var s = File.OpenRead(p);
                using var z = new ZipArchive(s, ZipArchiveMode.Read);
                return z.GetEntry("assets/bin/Data/globalgamemanagers") != null
                    || z.GetEntry("assets/bin/Data/data.unity3d") != null;
            })
            ?? extractedApks[0];

        string? arm64Split = extractedApks.FirstOrDefault(p =>
            p != baseApk &&
            (Path.GetFileName(p).Contains("arm64", StringComparison.OrdinalIgnoreCase) || HasArm64Libs(p)));

        string[] extraSplits = extractedApks
            .Where(p => p != baseApk && p != arm64Split)
            .ToArray();

        return new ExtractedSplitBundle(destinationDirectory, baseApk, arm64Split, extraSplits);
    }

    private static bool HasArm64Libs(string apkPath)
    {
        try
        {
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
