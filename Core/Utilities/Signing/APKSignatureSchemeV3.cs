using System.Collections.Generic;
using System.IO;
using System.Linq;

namespace MelonLoader.Installer.Core.Utilities.Signing
{
    public class APKSignatureSchemeV3
    {
        public static readonly uint ID = 0xf05368c0;

        public class Signer
        {
            public class BlockSignedData
            {
                public List<APKSignatureSchemeV2.Signer.BlockSignedData.Digest> Digests { get; } = [];
                public List<byte[]> Certificates { get; } = [];
                public int MinSdkVersion { get; set; } = 28;
                public int MaxSdkVersion { get; set; } = int.MaxValue;
                public List<APKSignatureSchemeV2.Signer.BlockSignedData.AdditionalAttribute> AdditionalAttributes { get; } = [];

                public void Write(FileMemory memory)
                {
                    memory.WriteUInt((uint)Digests.Sum(v => v.Length()));
                    Digests.ForEach(v => v.Write(memory));

                    memory.WriteUInt((uint)(Certificates.Count * 4 + Certificates.Sum(v => v.Length)));
                    Certificates.ForEach(v =>
                    {
                        memory.WriteUInt((uint)v.Length);
                        memory.WriteBytes(v);
                    });

                    memory.WriteInt(MinSdkVersion);
                    memory.WriteInt(MaxSdkVersion);

                    memory.WriteUInt((uint)AdditionalAttributes.Sum(v => v.Length()));
                    AdditionalAttributes.ForEach(v => v.Write(memory));
                }
            }

            public byte[]? SignedData { get; set; }
            public int MinSdkVersion { get; set; } = 28;
            public int MaxSdkVersion { get; set; } = int.MaxValue;
            public List<APKSignatureSchemeV2.Signer.BlockSignature> Signatures { get; } = [];
            public byte[]? PublicKey { get; set; }

            public int Length()
            {
                return 4
                    + 4 + (SignedData?.Length ?? 0)
                    + 4 + 4
                    + 4 + Signatures.Sum(v => v.Length())
                    + 4 + (PublicKey?.Length ?? 0);
            }

            public void Write(FileMemory memory)
            {
                memory.WriteUInt((uint)(Length() - 4));
                if (SignedData == null)
                {
                    memory.WriteUInt(0);
                }
                else
                {
                    memory.WriteUInt((uint)SignedData.Length);
                    memory.WriteBytes(SignedData);
                }

                memory.WriteInt(MinSdkVersion);
                memory.WriteInt(MaxSdkVersion);

                memory.WriteUInt((uint)Signatures.Sum(v => v.Length()));
                Signatures.ForEach(v => v.Write(memory));

                if (PublicKey == null)
                {
                    memory.WriteUInt(0);
                }
                else
                {
                    memory.WriteUInt((uint)PublicKey.Length);
                    memory.WriteBytes(PublicKey);
                }
            }
        }

        public List<Signer> Signers { get; } = [];

        public void Write(FileMemory memory)
        {
            memory.WriteUInt((uint)Signers.Sum(v => v.Length()));
            Signers.ForEach(v => v.Write(memory));
        }

        public APKSigningBlock.IDValuePair ToIDValuePair()
        {
            using MemoryStream ms = new();
            using FileMemory memory = new(ms);
            Write(memory);
            return new APKSigningBlock.IDValuePair(ID, ms.ToArray());
        }
    }
}
