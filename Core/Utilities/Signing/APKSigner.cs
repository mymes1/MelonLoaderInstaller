using Org.BouncyCastle.Asn1;
using Org.BouncyCastle.Asn1.Cms;
using Org.BouncyCastle.Asn1.X509;
using Org.BouncyCastle.Cms;
using Org.BouncyCastle.Crypto;
using Org.BouncyCastle.OpenSsl;
using Org.BouncyCastle.Security;
using Org.BouncyCastle.X509;
using Org.BouncyCastle.X509.Store;
using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Security.Cryptography;
using System.Text;

namespace MelonLoader.Installer.Core.Utilities.Signing
{
    public class APKSigner
    {
        private const int StreamBufferSize = 1024 * 1024;
        private X509Certificate _xCert = null!;
        private AsymmetricKeyParameter _privateKey = null!;
        private readonly SHA256 _sha;

        private readonly UTF8Encoding _encoding;
        private readonly IPatchLogger _logger;

        public APKSigner(string pemData, IPatchLogger patchLogger)
        {
            _encoding = new UTF8Encoding(false);
            _sha = SHA256.Create();
            _logger = patchLogger;

            LoadCerts(pemData);
        }

        private void LoadCerts(string pemData)
        {
            _logger.Log("Reading certificates");

            using (var reader = new StringReader(pemData))
            {
                var pemReader = new PemReader(reader);
                object? pemObject;
                while ((pemObject = pemReader.ReadObject()) != null)
                {
                    _xCert ??= pemObject as X509Certificate;
                    _privateKey ??= (pemObject as AsymmetricCipherKeyPair)?.Private;
                }
            }

            if (_xCert == null)
                throw new System.Security.SecurityException("Certificate could not be loaded from PEM data.");

            if (_privateKey == null)
                throw new System.Security.SecurityException("Private Key could not be loaded from PEM data.");
        }

        public void Sign(string apkPath)
        {
            _logger.Log("Aligning");
            APKAligner.AlignApk(apkPath);

            _logger.Log("Signing with V2+V3");
            SignV2(apkPath);

            _logger.Log("Done");
        }

        #region V1

        private void SignV1(string apkPath)
        {
            using FileStream zipStream = new(apkPath, FileMode.Open);
            using ZipArchive archive = new(zipStream, ZipArchiveMode.Read | ZipArchiveMode.Update);

            using MemoryStream manifestStream = new();
            using StreamWriter manifestWriter = AsStreamWriter(manifestStream);

            using MemoryStream sigHolderStream = new();
            using StreamWriter sigHolderWriter = AsStreamWriter(sigHolderStream);

            manifestWriter.WriteLine("Manifest-Version: 1.0");
            manifestWriter.WriteLine("Created-By: LemonLoader");
            manifestWriter.WriteLine();

            manifestWriter.Close();

            foreach (ZipArchiveEntry entry in archive.Entries)
            {
                if (entry.FullName.StartsWith("META-INF"))
                    continue;

                WriteDigests(entry, manifestStream, sigHolderWriter);
            }

            sigHolderWriter.Close();

            using MemoryStream sigStream = new();
            using StreamWriter sigWriter = AsStreamWriter(sigStream);

            manifestStream.Seek(0, SeekOrigin.Begin);
            byte[] manifestSha = _sha.ComputeHash(manifestStream);

            sigWriter.WriteLine("Signature-Version: 1.0");
            sigWriter.WriteLine("Created-By: LemonLoader");
            sigWriter.WriteLine($"SHA-256-Digest-Manifest: {Convert.ToBase64String(manifestSha)}");
            sigWriter.WriteLine();

            sigWriter.Close();

            sigHolderStream.Seek(0, SeekOrigin.Begin);
            sigHolderStream.CopyTo(sigStream);

            foreach (ZipArchiveEntry entry in archive.Entries.Where(a => a.FullName.StartsWith("META-INF")).ToArray())
                entry.Delete();

            manifestStream.Seek(0, SeekOrigin.Begin);
            sigStream.Seek(0, SeekOrigin.Begin);

            ZipArchiveEntry manifestEntry = archive.CreateEntry("META-INF/MANIFEST.MF");
            ZipArchiveEntry sigEntry = archive.CreateEntry("META-INF/LEMON.SF");
            ZipArchiveEntry rsaEntry = archive.CreateEntry("META-INF/LEMON.RSA");

            using Stream manifestEntryStream = manifestEntry.Open();
            manifestStream.CopyTo(manifestEntryStream);

            using Stream sigEntryStream = sigEntry.Open();
            sigStream.CopyTo(sigEntryStream);

            sigStream.Seek(0, SeekOrigin.Begin);

            byte[] signedSig = GetSignatureFileSig(sigStream);
            using Stream rsaEntryStream = rsaEntry.Open();
            rsaEntryStream.Write(signedSig);
        }

        private void WriteDigests(ZipArchiveEntry entry, Stream manifestStream, StreamWriter sigHolderWriter)
        {
            using Stream entryStream = entry.Open();
            string entryDigest = Convert.ToBase64String(_sha.ComputeHash(entryStream));

            using MemoryStream proxyStream = new();
            using StreamWriter proxyWriter = AsStreamWriter(proxyStream);

            proxyWriter.WriteLine($"Name: {entry.FullName}");
            proxyWriter.WriteLine($"SHA-256-Digest: {entryDigest}");
            proxyWriter.WriteLine();

            proxyWriter.Close();

            proxyStream.Seek(0, SeekOrigin.Begin);

            sigHolderWriter.WriteLine($"Name: {entry.FullName}");
            sigHolderWriter.WriteLine($"SHA-256-Digest: {Convert.ToBase64String(_sha.ComputeHash(proxyStream))}");
            sigHolderWriter.WriteLine();
            proxyStream.Seek(0, SeekOrigin.Begin);
            proxyStream.CopyTo(manifestStream);
        }

        private byte[] GetSignatureFileSig(Stream sfStream)
        {
            var certStore = X509StoreFactory.Create("Certificate/Collection", new X509CollectionStoreParameters(new List<X509Certificate> { _xCert }));
            CmsSignedDataGenerator dataGen = new();
            dataGen.AddCertificates(certStore);
            dataGen.AddSigner(_privateKey, _xCert, CmsSignedGenerator.EncryptionRsa, CmsSignedGenerator.DigestSha256);

            CmsProcessableInputStream detachedContent = new(sfStream);
            var signedContent = dataGen.Generate(detachedContent, false);

            var signerInfos = signedContent.GetSignerInfos();
            var signer = signerInfos.GetSigners().Cast<SignerInformation>().First();
            SignerInfo signerInfo = signer.ToSignerInfo();
            Asn1EncodableVector digestAlgorithmsVector =
            [
                new AlgorithmIdentifier(new DerObjectIdentifier("2.16.840.1.101.3.4.2.1"), DerNull.Instance)
            ];
            ContentInfo encapContentInfo = new(new DerObjectIdentifier("1.2.840.113549.1.7.1"), null);
            Asn1EncodableVector asnVector =
            [
                X509CertificateStructure.GetInstance(Asn1Object.FromByteArray(_xCert.GetEncoded()))
            ];
            Asn1EncodableVector signersVector = [signerInfo.ToAsn1Object()];
            SignedData signedData = new(new DerSet(digestAlgorithmsVector), encapContentInfo, new BerSet(asnVector), null, new DerSet(signersVector));
            ContentInfo contentInfo = new(new DerObjectIdentifier("1.2.840.113549.1.7.2"), signedData);
            return contentInfo.GetDerEncoded();
        }

        private StreamWriter AsStreamWriter(MemoryStream ms) => new(ms, _encoding, 1024, true);

        #endregion

        #region V2/V3

        private void SignV2(string path)
        {
            string dir = Path.GetDirectoryName(Path.GetFullPath(path)) ?? Path.GetTempPath();
            string tempPath = Path.Combine(dir, $".sign_{Guid.NewGuid():N}.tmp");

            try
            {
                using (FileStream fs = new(path, FileMode.Open, FileAccess.Read, FileShare.Read))
                using (FileMemory memory = new(fs))
                {
                    long eocdPosition = APKAligner.FindEndOfCentralDirectory(memory);
                    memory.Position = eocdPosition;
                    EndOfCentralDirectory eocd = new(memory);

                    long cdOffset = eocd.OffsetOfCD;
                    if (cdOffset == uint.MaxValue)
                        throw new InvalidOperationException("APK Central Directory offset exceeds 4GB ZIP32 limit supported by APK Signature Scheme v2/v3.");

                    // Strip any pre-existing APK Signing Block immediately preceding Central Directory
                    long contentEnd = cdOffset;
                    if (cdOffset >= 24)
                    {
                        memory.Position = cdOffset - 24;
                        ulong existingSize = memory.ReadULong();
                        string magic = memory.ReadString(16);
                        if (magic == APKSigningBlock.MAGIC && existingSize >= 24 && (long)(existingSize + 8) <= cdOffset)
                        {
                            contentEnd = cdOffset - (long)(existingSize + 8);
                        }
                    }

                    // Read EOCD bytes and normalize OffsetOfCD to contentEnd for section4 digest (per Android APK Sig v2/v3 spec)
                    fs.Position = eocdPosition;
                    int eocdLen = checked((int)(fs.Length - eocdPosition));
                    byte[] eocdForDigest = new byte[eocdLen];
                    fs.ReadExactly(eocdForDigest, 0, eocdLen);
                    BitConverter.TryWriteBytes(eocdForDigest.AsSpan(16, 4), (uint)contentEnd);

                    var section1 = GetSectionDigests(fs, 0, contentEnd);
                    var section3 = GetSectionDigests(fs, cdOffset, eocdPosition);
                    var section4 = GetBufferDigests(eocdForDigest);

                    int totalChunks = section1.Count + section3.Count + section4.Count;
                    byte[] topLevelInput = new byte[5 + totalChunks * 32];
                    topLevelInput[0] = 0x5a;
                    BitConverter.TryWriteBytes(topLevelInput.AsSpan(1, 4), (uint)totalChunks);
                    int offset = 5;
                    foreach (var d in section1) { Buffer.BlockCopy(d, 0, topLevelInput, offset, 32); offset += 32; }
                    foreach (var d in section3) { Buffer.BlockCopy(d, 0, topLevelInput, offset, 32); offset += 32; }
                    foreach (var d in section4) { Buffer.BlockCopy(d, 0, topLevelInput, offset, 32); offset += 32; }

                    byte[] digest = _sha.ComputeHash(topLevelInput);

                    const uint algorithm = 0x0103; // RSA_PKCS1_V1_5_WITH_SHA256
                    byte[] certEncoded = _xCert.GetEncoded();
                    byte[] publicKeyEncoded = _xCert.CertificateStructure.SubjectPublicKeyInfo.GetDerEncoded();

                    // Build V2 signer block
                    APKSignatureSchemeV2 v2Block = new();
                    APKSignatureSchemeV2.Signer v2Signer = new();
                    using (MemoryStream signedDataMs = new())
                    using (FileMemory memorySignedData = new(signedDataMs))
                    {
                        var signedData = new APKSignatureSchemeV2.Signer.BlockSignedData();
                        signedData.Digests.Add(new APKSignatureSchemeV2.Signer.BlockSignedData.Digest(algorithm, digest));
                        signedData.Certificates.Add(certEncoded);
                        signedData.Write(memorySignedData);
                        v2Signer.SignedData = signedDataMs.ToArray();
                    }

                    v2Signer.Signatures.Add(new APKSignatureSchemeV2.Signer.BlockSignature(algorithm, SignBytes(v2Signer.SignedData)));
                    v2Signer.PublicKey = publicKeyEncoded;
                    v2Block.Signers.Add(v2Signer);

                    // Build V3 signer block (Android 9+ / API 28+)
                    APKSignatureSchemeV3 v3Block = new();
                    APKSignatureSchemeV3.Signer v3Signer = new()
                    {
                        MinSdkVersion = 28,
                        MaxSdkVersion = int.MaxValue,
                        PublicKey = publicKeyEncoded
                    };
                    using (MemoryStream v3SignedDataMs = new())
                    using (FileMemory v3SignedMem = new(v3SignedDataMs))
                    {
                        var v3SignedData = new APKSignatureSchemeV3.Signer.BlockSignedData
                        {
                            MinSdkVersion = 28,
                            MaxSdkVersion = int.MaxValue
                        };
                        v3SignedData.Digests.Add(new APKSignatureSchemeV2.Signer.BlockSignedData.Digest(algorithm, digest));
                        v3SignedData.Certificates.Add(certEncoded);
                        v3SignedData.Write(v3SignedMem);
                        v3Signer.SignedData = v3SignedDataMs.ToArray();
                    }

                    v3Signer.Signatures.Add(new APKSignatureSchemeV2.Signer.BlockSignature(algorithm, SignBytes(v3Signer.SignedData)));
                    v3Block.Signers.Add(v3Signer);

                    APKSigningBlock signingBlock = new();
                    signingBlock.Values.Add(v2Block.ToIDValuePair());
                    signingBlock.Values.Add(v3Block.ToIDValuePair());
                    signingBlock.PadToMultiple(4096);

                    long newCdOffset = contentEnd + (long)signingBlock.TotalByteLength();
                    if (newCdOffset >= uint.MaxValue)
                        throw new InvalidOperationException("Signed APK Central Directory offset exceeds 4GB limit.");

                    eocd.OffsetOfCD = (uint)newCdOffset;

                    using (FileStream outFs = new(tempPath, FileMode.Create, FileAccess.Write, FileShare.None))
                    using (FileMemory outMemory = new(outFs))
                    {
                        byte[] copyBuffer = new byte[StreamBufferSize];

                        fs.Position = 0;
                        FileMemory.CopyExact(fs, outFs, contentEnd, copyBuffer);

                        signingBlock.Write(outMemory);

                        fs.Position = cdOffset;
                        FileMemory.CopyExact(fs, outFs, eocdPosition - cdOffset, copyBuffer);

                        eocd.Write(outMemory);
                        outFs.Flush();
                    }
                }

                File.Move(tempPath, path, overwrite: true);
            }
            finally
            {
                try
                {
                    if (File.Exists(tempPath))
                        File.Delete(tempPath);
                }
                catch { }
            }
        }

        private byte[] SignBytes(byte[] data)
        {
            ISigner signerType = SignerUtilities.GetSigner("SHA256WithRSA");
            signerType.Init(true, _privateKey);
            signerType.BlockUpdate(data, 0, data.Length);
            return signerType.GenerateSignature();
        }

        private List<byte[]> GetSectionDigests(FileStream fs, long startOffset, long endOffset)
        {
            var digests = new List<byte[]>();
            const int chunkSize = 1024 * 1024;
            byte[] buffer = new byte[5 + chunkSize];
            buffer[0] = 0xa5;

            for (long i = startOffset; i < endOffset; i += chunkSize)
            {
                fs.Position = i;
                int size = (int)Math.Min(endOffset - i, chunkSize);
                BitConverter.TryWriteBytes(buffer.AsSpan(1, 4), (uint)size);
                fs.ReadExactly(buffer, 5, size);
                digests.Add(_sha.ComputeHash(buffer, 0, 5 + size));
            }
            return digests;
        }

        private List<byte[]> GetBufferDigests(byte[] data)
        {
            var digests = new List<byte[]>();
            const int chunkSize = 1024 * 1024;
            byte[] buffer = new byte[5 + Math.Min(data.Length, chunkSize)];
            buffer[0] = 0xa5;

            for (int i = 0; i < data.Length; i += chunkSize)
            {
                int size = Math.Min(data.Length - i, chunkSize);
                BitConverter.TryWriteBytes(buffer.AsSpan(1, 4), (uint)size);
                Buffer.BlockCopy(data, i, buffer, 5, size);
                digests.Add(_sha.ComputeHash(buffer, 0, 5 + size));
            }
            return digests;
        }

        #endregion
    }
}
