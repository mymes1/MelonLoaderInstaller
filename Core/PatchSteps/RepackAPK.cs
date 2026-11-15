using System;
using System.Data;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text;
using System.Xml;

namespace MelonLoader.Installer.Core.PatchSteps;

internal class RepackAPK : IPatchStep
{
    public bool Run(Patcher patcher)
    {
        patcher.Logger.Log("Repacking APK");

        using FileStream zipStream = new(patcher.Info.OutputBaseApkPath, FileMode.Open);
        ZipArchive archive = new(zipStream, ZipArchiveMode.Read | ZipArchiveMode.Update);

        // Handle old installer files
        var dexEntries = archive.Entries.Where(a => a.FullName.Contains("originalDex")).ToArray();
        if (dexEntries.Length > 0)
        {
            patcher.Logger.Log("Found remnants of Java patching, replacing patched dex");
            for (int i = dexEntries.Length - 1; i >= 0; i--)
            {
                ZipArchiveEntry dex = dexEntries[i];
                string path = Path.Combine(patcher.Args.TempDirectory, dex.Name);
                dex.ExtractToFile(path, overwrite: true);
                byte[] dexData = File.ReadAllBytes(path);

                dex.Delete();
                ZipArchiveEntry realDexEntry = archive.GetEntry(Path.GetFileName(path))!;
                using Stream realDexStream = realDexEntry.Open();
                using MemoryStream dexStream = new(dexData);
                dexStream.CopyTo(realDexStream);
            }

            patcher.Logger.Log("Done");
        }

        patcher.Logger.Log("Copying data into APK");

        // assets/ data
        CopyTo(archive, Path.Combine(patcher.Info.LemonDataDirectory, "MelonLoader"), "assets/MelonLoader");
        CopyTo(archive, Path.Combine(patcher.Info.LemonDataDirectory, "dotnet"), "assets/dotnet");
        WritePatchDate(archive);
        WriteStageConfig(archive, patcher.Args.PatchStage);
        patcher.Logger.Log($"Configured LemonLoader native stage: {patcher.Args.PatchStage.ToAssetToken()}");

        string unityOverrideDir = Path.Combine(patcher.Info.UnityNativeDirectory, "arm64-v8a");
        bool hasUnityOverride = Directory.Exists(unityOverrideDir) && Directory.GetFiles(unityOverrideDir, "*.so", SearchOption.AllDirectories).Length > 0;

        string nativeDir = Path.Combine(patcher.Info.LemonDataDirectory, "native");
        bool isSingleMainNativeAot = !File.Exists(Path.Combine(nativeDir, "libBootstrap.so"));

        // libs data
        if (string.IsNullOrEmpty(patcher.Info.OutputLibApkPath))
        {
            if (isSingleMainNativeAot)
            {
                archive.GetEntry("lib/arm64-v8a/libBootstrap.so")?.Delete();
                archive.GetEntry("lib/arm64-v8a/libdobby.so")?.Delete();
            }
            CopyTo(archive, nativeDir, "lib/arm64-v8a", "*.so");
            if (hasUnityOverride)
                CopyTo(archive, unityOverrideDir, "lib/arm64-v8a", "*.so");
            else
                patcher.Logger.Log("Keeping original APK lib/arm64-v8a/libunity.so untouched.");
        }
        else
        {
            using FileStream libStream = File.Open(patcher.Info.OutputLibApkPath, FileMode.Open);
            using ZipArchive libArchive = new(libStream, ZipArchiveMode.Read | ZipArchiveMode.Update);

            if (isSingleMainNativeAot)
            {
                libArchive.GetEntry("lib/arm64-v8a/libBootstrap.so")?.Delete();
                libArchive.GetEntry("lib/arm64-v8a/libdobby.so")?.Delete();
            }
            CopyTo(libArchive, nativeDir, "lib/arm64-v8a", "*.so");
            if (hasUnityOverride)
                CopyTo(libArchive, unityOverrideDir, "lib/arm64-v8a", "*.so");
            else
                patcher.Logger.Log("Keeping original split APK lib/arm64-v8a/libunity.so untouched.");
        }

        patcher.Logger.Log("Writing, this can take a few");

        // for whatever reason ZipArchive uses disposal as the only time to save
        archive.Dispose();

        patcher.Logger.Log("Done");

        return true;
    }

    private static void CopyTo(ZipArchive archive, string source, string dest, string matcher = "*.*")
    {
        foreach (string file in Directory.GetFiles(source, matcher, SearchOption.AllDirectories))
        {
            string entryPath = Path.Combine(dest, Path.GetRelativePath(source, file)).Replace('\\', '/');

            // I don't think this is supposed to be needed, but I had an issue with an apk having two libmain.so files
            ZipArchiveEntry? entry = archive.GetEntry(entryPath);
            entry?.Delete();

            CompressionLevel level = file.EndsWith(".so", StringComparison.OrdinalIgnoreCase)
                ? CompressionLevel.NoCompression
                : CompressionLevel.Optimal;
            entry = archive.CreateEntry(entryPath, level);
            using FileStream sourceStream = File.OpenRead(file);
            using Stream entryStream = entry.Open();
            sourceStream.CopyTo(entryStream);
        }
    }

    private static void WriteStageConfig(ZipArchive archive, LemonPatchStage stage)
    {
        const string entryPath = "assets/lemon_stage.txt";
        ZipArchiveEntry? entry = archive.GetEntry(entryPath);
        entry?.Delete();

        byte[] bytes = Encoding.UTF8.GetBytes(stage.ToAssetToken());
        entry = archive.CreateEntry(entryPath, CompressionLevel.Optimal);
        using Stream entryStream = entry.Open();
        entryStream.Write(bytes);
    }

    private static void WritePatchDate(ZipArchive archive)
    {
        string entryPath = "assets/lemon_patch_date.txt";
        ZipArchiveEntry? entry = archive.GetEntry(entryPath);
        entry?.Delete();

        string rfc3339 = XmlConvert.ToString(DateTime.Now, XmlDateTimeSerializationMode.Utc);
        byte[] bytes = Encoding.UTF8.GetBytes(rfc3339);

        entry = archive.CreateEntry(entryPath, CompressionLevel.Optimal);
        using Stream entryStream = entry.Open();
        entryStream.Write(bytes);
    }
}
