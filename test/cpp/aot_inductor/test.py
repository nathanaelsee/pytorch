
import torch
from torch._export import aot_compile, dynamic_dim

torch.manual_seed(1337)

class Net(torch.nn.Module):
    def __init__(self, device):
        super().__init__()
        self.w_pre = torch.randn(4, 4, device=device)
        self.w_add = torch.randn(4, 4, device=device)

    def forward(self, x):
        w_transpose = torch.transpose(self.w_pre, 0, 1)
        w_relu = torch.nn.functional.relu(w_transpose)
        w = w_relu + self.w_add
        return torch.matmul(x, w)

data = {}

for device in ["cpu", "cuda"]:
    for use_runtime_constant_folding in [True, False]:
        if device == "cpu" and use_runtime_constant_folding:
            # We do not test runtime const folding for cpu mode.
            continue
        model = Net(device).to(device=device)
        x = torch.randn((4, 4), device=device)
        with torch.no_grad():
            ref_output = model(x)

        torch._dynamo.reset()
        with torch.no_grad():
            constraints = [
                dynamic_dim(x, 0) >= 1,
                dynamic_dim(x, 0) <= 1024,
            ]
            model_so_path = aot_compile(
                model,
                (x,),
                constraints=constraints,
                options={"use_runtime_constant_folding": use_runtime_constant_folding})

        suffix = f"{device}"
        if use_runtime_constant_folding:
            suffix += "_use_runtime_constant_folding"
        data.update({
            f"model_so_path_{suffix}": model_so_path,
            f"inputs_{suffix}": [x],
            f"outputs_{suffix}": [ref_output],
            f"w_pre_{suffix}": model.w_pre,
            f"w_add_{suffix}": model.w_add,
        })

# Use this to communicate tensors to the cpp code
class Serializer(torch.nn.Module):
    def __init__(self, data):
        super().__init__()
        for key in data:
            setattr(self, key, data[key])

torch.jit.script(Serializer(data)).save("data.pt")
