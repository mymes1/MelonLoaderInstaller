using System;
using System.IO;

namespace MelonLoader.Installer.Core.PatchSteps;

internal class CleanUp : IPatchStep
{
    public bool Run(Patcher patcher)
    {
        patcher.Logger.Log("Cleaning up");

        try
        {
            if (IsInsideDirectory(patcher.Args.MelonDataPath, patcher.Args.TempDirectory))
                DeleteFile(patcher.Args.MelonDataPath);
            if (IsInsideDirectory(patcher.Args.UnityDependenciesPath, patcher.Args.TempDirectory))
                DeleteFile(patcher.Args.UnityDependenciesPath);

            DeleteDir(patcher.Info.LemonDataDirectory);
            DeleteDir(patcher.Info.UnityNativeDirectory);

            string extraLibPath = Path.Combine(patcher.Args.TempDirectory, "extraLibraries.zip");
            DeleteFile(extraLibPath);
        }
        catch
        {
            patcher.Logger.Log("Failed to clean up leftover data.");
        }

        return true;
    }

    private static bool IsInsideDirectory(string? filePath, string? dirPath)
    {
        if (string.IsNullOrWhiteSpace(filePath) || string.IsNullOrWhiteSpace(dirPath))
            return false;

        string fullFile = Path.GetFullPath(filePath);
        string fullDir = Path.GetFullPath(dirPath).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
            + Path.DirectorySeparatorChar;
        return fullFile.StartsWith(fullDir, StringComparison.OrdinalIgnoreCase);
    }

    private static void DeleteFile(string path)
    {
        if (File.Exists(path))
            File.Delete(path);
    }

    private static void DeleteDir(string path)
    {
        if (Directory.Exists(path))
            Directory.Delete(path, true);
    }
}
