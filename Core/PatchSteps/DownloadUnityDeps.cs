using System;
using System.IO;
using System.Net.Http;
using System.Threading.Tasks;

namespace MelonLoader.Installer.Core.PatchSteps;

internal class DownloadUnityDeps : IPatchStep
{
    private const string LIBUNITY_URL_TEMPLATE = "https://github.com/LavaGang/MelonLoader.UnityDependencies/releases/download/{0}/libunity.so.{1}";
    private const string CHINA_LIBUNITY_URL_TEMPLATE = "https://github.com/LemonLoader/MelonLoader.UnityDependencies.China/releases/download/{0}/libunity.so.{1}";

    public bool Run(Patcher patcher)
    {
        if (!string.IsNullOrEmpty(patcher.Args.UnityDependenciesPath) && File.Exists(patcher.Args.UnityDependenciesPath))
        {
            patcher.Logger.Log("Using local Unity dependencies!");
            return true;
        }

        if (!patcher.Args.UnityVersion.HasValue)
        {
            patcher.Logger.Log("Got to DownloadUnityDeps while UnityVersion is null, how did this happen?");
            return false;
        }

        string unityVersion = patcher.Args.UnityVersion.Value.ToStringWithoutType();
        string libDir = Path.Combine(patcher.Info.UnityNativeDirectory, "arm64-v8a");
        if (!Directory.Exists(libDir))
            Directory.CreateDirectory(libDir);

        string libUnityOutPath = Path.Combine(libDir, "libunity.so");
        string cacheDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "MelonLoader.Installer.App", "UnityCache", unityVersion);
        string cachedLibUnity = Path.Combine(cacheDir, "libunity.so.arm64-v8a");

        if (File.Exists(cachedLibUnity) && new FileInfo(cachedLibUnity).Length > 0)
        {
            patcher.Logger.Log($"Using cached Unity {unityVersion} arm64-v8a dependencies");
            File.Copy(cachedLibUnity, libUnityOutPath, true);
            return true;
        }

        patcher.Logger.Log($"Downloading Unity {unityVersion} arm64-v8a dependencies");

        using HttpClient client = new();
        client.DefaultRequestHeaders.UserAgent.ParseAdd("MelonLoaderInstaller/1.0");
        client.Timeout = TimeSpan.FromMinutes(5);

        string url = patcher.Args.UnityVersion.Value.Type == AssetRipper.Primitives.UnityVersionType.China
            ? CHINA_LIBUNITY_URL_TEMPLATE
            : LIBUNITY_URL_TEMPLATE;
        string formattedUrl = string.Format(url, unityVersion, "arm64-v8a");

        try
        {
            patcher.Logger.Log($"Fetching [ {formattedUrl} ]");
            byte[] data = Task.Run(() => client.GetByteArrayAsync(formattedUrl)).GetAwaiter().GetResult();
            File.WriteAllBytes(libUnityOutPath, data);

            try
            {
                Directory.CreateDirectory(cacheDir);
                File.WriteAllBytes(cachedLibUnity, data);
            }
            catch { }

            patcher.Logger.Log($"Downloaded libunity.so ({data.Length:N0} bytes)");
        }
        catch (Exception ex)
        {
            patcher.Logger.Log("Failed to download Unity dependencies. You may need to provide a local Unity dependencies zip.\n" + ex);
            return false;
        }

        return true;
    }
}
