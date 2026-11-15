using System.Collections.Generic;
using System.Linq;

namespace MelonLoader.Installer.Core.Utilities.Signing
{
    public class APKSigningBlock
    {
        public class IDValuePair
        {
            public uint ID { get; private set; }
            public int Value { get; private set; }
            public byte[]? Data { get; private set; }

            public IDValuePair(uint id, int value)
            {
                ID = id;
                Value = value;
                Data = null;
            }

            public IDValuePair(uint id, byte[] value)
            {
                ID = id;
                Value = value.Length;
                Data = value;
            }

            public int Length()
            {
                return 8 + 4 + (Data?.Length ?? 4);
            }

            public void Write(FileMemory memory)
            {
                memory.WriteULong((ulong)Length() - 8);
                memory.WriteUInt(ID);
                if (Data == null)
                {
                    memory.WriteInt(Value);
                }
                else
                {
                    memory.WriteBytes(Data);
                }
            }
        }

        public static readonly string MAGIC = "APK Sig Block 42";
        public static readonly uint VERITY_PADDING_BLOCK_ID = 0x42726577;

        public List<IDValuePair> Values { get; private set; }

        public APKSigningBlock()
        {
            Values = new List<IDValuePair>();
        }

        public ulong TotalByteLength()
        {
            return (ulong)Values.Sum(v => (long)v.Length()) + 32UL;
        }

        public void PadToMultiple(int alignment = 4096)
        {
            ulong total = TotalByteLength();
            ulong rem = total % (ulong)alignment;
            if (rem == 0)
                return;

            ulong needed = (ulong)alignment - rem;
            if (needed < 12)
                needed += (ulong)alignment;

            Values.Add(new IDValuePair(VERITY_PADDING_BLOCK_ID, new byte[(int)(needed - 12)]));
        }

        public void Write(FileMemory memory)
        {
            ulong size = (ulong)Values.Sum(values => (long)values.Length()) + 8UL + 16UL;
            memory.WriteULong(size);
            Values.ForEach(value => value.Write(memory));
            memory.WriteULong(size);
            memory.WriteString(MAGIC);
        }
    }
}
