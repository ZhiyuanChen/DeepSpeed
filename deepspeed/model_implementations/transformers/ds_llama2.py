# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: Apache-2.0

# DeepSpeed Team

import torch
import torch.nn as nn
from deepspeed import comm as dist
from deepspeed.utils.logging import log_dist

from deepspeed.ops.transformer.inference.ds_mlp import DeepSpeedMLP
from deepspeed.ops.transformer.inference.ds_attention import DeepSpeedSelfAttention, BloomSelfAttention
from deepspeed.accelerator import get_accelerator
from deepspeed.ops.op_builder import InferenceBuilder
import deepspeed
if deepspeed.HAS_TRITON:
    from deepspeed.ops.transformer.inference.triton.mlp import TritonMLP
    from deepspeed.ops.transformer.inference.triton.attention import TritonSelfAttention

inference_module = None

from deepspeed.model_implementations.transformers.ds_transformer import DeepSpeedTransformerInference

class DeepSpeedLlama2Inference(DeepSpeedTransformerInference):
    """Initialize the DeepSpeed OPT Transformer Layer.
    """

    def __init__(self,
                 config,
                 mp_group=None,
                 quantize_scales=None,
                 quantize_groups=1,
                 merge_count=1,
                 mlp_extra_grouping=False):
        super().__init__(config, mp_group, quantize_scales, quantize_groups, merge_count, mlp_extra_grouping)

    def forward(
            self,
            *args,
            **kwargs):

        input = args[0]
        input_mask = None
        # Allocate memory only on first layer forward
        if self.config.layer_id == 0 and self._alloc_workspace:
            self.allocate_workspace(self.config.hidden_size, self.config.heads,
                                    input.size()[1],
                                    input.size()[0], DeepSpeedTransformerInference.layer_id, self.config.mp_size,
                                    self.config.bigscience_bloom,
                                    dist.get_rank() if dist.is_initialized() else 0, self.config.max_out_tokens,
                                    self.config.min_out_tokens)
            self._alloc_workspace = False

        get_present = True

        # We set the prev key/value to None when there is a prompt
        if input.shape[1] > 1:
            self.layer_past = None
        layer_past = self.layer_past

        input_type = input.dtype

        if (self.config.dtype in [torch.float16, torch.bfloat16, torch.int8]) \
            and input.dtype == torch.float:
            target_dtype = torch.half if self.dtype == torch.int8 else self.dtype
            input = input.to(target_dtype)

        with torch.no_grad():
            attention_output, key, value, context_outputtn_ctx, inp_norm = \
                                     self.attention(input,
                                              input_mask,
                                              None,
                                              layer_past,
                                              get_present,
                                              None, None, None,
                                              self.norm_w,
                                              self.norm_b,
                                              None)
            self.layer_past = (key, value)
            output = self.mlp(attention_output, input, inp_norm, self.attention.attn_ob)

            output = output.to(input_type)
        #print(f'{self.config.layer_id}: {output.norm()}, {output.shape}')
        #exit()
        return output
