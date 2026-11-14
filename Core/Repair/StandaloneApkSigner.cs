using MelonLoader.Installer.Core.PatchSteps;
using MelonLoader.Installer.Core.Utilities.Signing;
using System;
using System.IO;

namespace MelonLoader.Installer.Core.Repair;

public static class StandaloneApkSigner
{
    public static void SignApk(string inputApkPath, string outputApkPath, IPatchLogger logger)
    {
        if (!File.Exists(inputApkPath))
            throw new FileNotFoundException("APK file not found.", inputApkPath);

        string tempWork = Path.Combine(Path.GetTempPath(), "lemon_sign_" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(tempWork);

        try
        {
            string workApk = Path.Combine(tempWork, Path.GetFileName(inputApkPath));
            File.Copy(inputApkPath, workApk, overwrite: true);

            var dummyArgs = new PatchArguments(
                workApk,
                "",
                [],
                tempWork,
                tempWork,
                "",
                "",
                null,
                "",
                false);
            var dummyPatcher = new Patcher(dummyArgs, logger);

            var certStep = new GenerateCertificate();
            if (!certStep.Run(dummyPatcher) || string.IsNullOrEmpty(dummyPatcher.Info.PemData))
                throw new InvalidOperationException("Failed to generate signing certificate.");

            var signer = new APKSigner(dummyPatcher.Info.PemData, logger);
            signer.Sign(workApk);

            string? outDir = Path.GetDirectoryName(Path.GetFullPath(outputApkPath));
            if (!string.IsNullOrEmpty(outDir))
                Directory.CreateDirectory(outDir);

            File.Copy(workApk, outputApkPath, overwrite: true);
            logger.Log($"Signed APK saved to [ {outputApkPath} ]");
        }
        finally
        {
            try { Directory.Delete(tempWork, true); } catch { }
        }
    }
}
