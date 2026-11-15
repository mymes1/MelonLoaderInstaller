namespace MelonLoader.Installer.Core.Utilities.Signing
{
    public class DataDescriptor
    {
        public static readonly int SIGNATURE = 0x08074b50;

        public bool HasSignature { get; set; }
        public bool IsZip64 { get; set; }
        public uint CRC32 { get; set; }
        public ulong CompressedSize { get; set; }
        public ulong UncompressedSize { get; set; }

        public DataDescriptor(FileMemory memory, bool isZip64 = false)
        {
            IsZip64 = isZip64;
            int first = memory.ReadInt();
            if (first == SIGNATURE)
            {
                HasSignature = true;
                CRC32 = memory.ReadUInt();
            }
            else
            {
                // ZIP spec allows Data Descriptor without the optional 0x08074b50 magic
                HasSignature = false;
                CRC32 = unchecked((uint)first);
            }

            if (isZip64)
            {
                CompressedSize = memory.ReadULong();
                UncompressedSize = memory.ReadULong();
            }
            else
            {
                CompressedSize = memory.ReadUInt();
                UncompressedSize = memory.ReadUInt();
            }
        }

        public void Write(FileMemory memory)
        {
            if (HasSignature)
                memory.WriteInt(SIGNATURE);
            memory.WriteUInt(CRC32);
            if (IsZip64)
            {
                memory.WriteULong(CompressedSize);
                memory.WriteULong(UncompressedSize);
            }
            else
            {
                memory.WriteUInt((uint)CompressedSize);
                memory.WriteUInt((uint)UncompressedSize);
            }
        }
    }
}
