//! Lossless decoders whose output cannot exceed the declared tile size.
use async_tiff::decoder::{Decoder, DecoderRegistry};
use async_tiff::error::{AsyncTiffError, AsyncTiffResult};
use async_tiff::tags::{Compression, PhotometricInterpretation};
use bytes::Bytes;
use std::io::Read;

#[derive(Debug)]
struct BoundedDecoder {
    compression: Compression,
    limit: usize,
}

fn error() -> AsyncTiffError {
    AsyncTiffError::General("invalid or oversized TIFF compression stream".into())
}

impl Decoder for BoundedDecoder {
    fn decode_tile(
        &self,
        buffer: Bytes,
        _: PhotometricInterpretation,
        _: Option<&[u8]>,
        _: u16,
        _: u16,
        _: Option<&[u32]>,
    ) -> AsyncTiffResult<Vec<u8>> {
        let mut output = Vec::new();
        match self.compression {
            Compression::None => output.extend_from_slice(&buffer),
            Compression::Deflate | Compression::OldDeflate => {
                flate2::read::ZlibDecoder::new(buffer.as_ref())
                    .take(self.limit as u64 + 1)
                    .read_to_end(&mut output)?;
            }
            Compression::ZSTD => {
                let mut decoder = zstd::stream::read::Decoder::new(buffer.as_ref())?;
                decoder.window_log_max(24)?;
                decoder
                    .take(self.limit as u64 + 1)
                    .read_to_end(&mut output)?;
            }
            Compression::LZW => {
                let mut decoder =
                    weezl::decode::Decoder::with_tiff_size_switch(weezl::BitOrder::Msb, 8);
                output.resize(self.limit + 1, 0);
                let (mut input, mut written) = (0, 0);
                loop {
                    let result = decoder.decode_bytes(&buffer[input..], &mut output[written..]);
                    input += result.consumed_in;
                    written += result.consumed_out;
                    if written > self.limit {
                        return Err(error());
                    }
                    match result.status.map_err(|_| error())? {
                        weezl::LzwStatus::Done => break,
                        weezl::LzwStatus::NoProgress => return Err(error()),
                        weezl::LzwStatus::Ok => (),
                    }
                }
                output.truncate(written);
            }
            _ => return Err(error()),
        }
        if output.len() > self.limit {
            return Err(error());
        }
        Ok(output)
    }
}

pub(super) fn registry(compression: Compression, limit: usize) -> DecoderRegistry {
    let mut registry = DecoderRegistry::empty();
    registry
        .as_mut()
        .insert(compression, Box::new(BoundedDecoder { compression, limit }));
    registry
}
