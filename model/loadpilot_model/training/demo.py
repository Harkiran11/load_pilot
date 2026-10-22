"""Train a small PyTorch classifier on reproducible synthetic data."""

import argparse
from pathlib import Path

import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset


def select_device(requested: str) -> torch.device:
    if requested == "auto":
        if torch.cuda.is_available():
            return torch.device("cuda")
        if torch.backends.mps.is_available():
            return torch.device("mps")
        return torch.device("cpu")
    if requested == "cuda" and not torch.cuda.is_available():
        raise ValueError("CUDA is unavailable. Use --device cpu or --device auto.")
    if requested == "mps" and not torch.backends.mps.is_available():
        raise ValueError("MPS is unavailable. Use --device cpu or --device auto.")
    return torch.device(requested)


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--epochs", type=positive_int, default=10)
    parser.add_argument("--batch-size", type=positive_int, default=64)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--device", choices=("auto", "cpu", "mps", "cuda"), default="auto")
    parser.add_argument("--output", type=Path, default=Path("artifacts/model.pt"))
    args = parser.parse_args()

    torch.manual_seed(args.seed)
    device = select_device(args.device)
    print(f"PyTorch {torch.__version__} | device: {device}")

    # Replace these synthetic features and labels with your own dataset.
    features = torch.randn(1280, 10)
    labels = (features[:, 0] + 0.5 * features[:, 1] > 0).long()
    train_loader = DataLoader(
        TensorDataset(features[:1024], labels[:1024]),
        batch_size=args.batch_size,
        shuffle=True,
    )
    validation_loader = DataLoader(
        TensorDataset(features[1024:], labels[1024:]),
        batch_size=args.batch_size,
    )

    model = nn.Sequential(nn.Linear(10, 32), nn.ReLU(), nn.Linear(32, 2)).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.01)
    criterion = nn.CrossEntropyLoss()

    for epoch in range(args.epochs):
        model.train()
        loss_sum = 0.0
        for batch_features, batch_labels in train_loader:
            batch_features, batch_labels = batch_features.to(device), batch_labels.to(device)
            optimizer.zero_grad(set_to_none=True)
            loss = criterion(model(batch_features), batch_labels)
            loss.backward()
            optimizer.step()
            loss_sum += loss.item() * len(batch_labels)

        model.eval()
        correct = 0
        with torch.inference_mode():
            for batch_features, batch_labels in validation_loader:
                predictions = model(batch_features.to(device)).argmax(dim=1)
                correct += (predictions == batch_labels.to(device)).sum().item()
        accuracy = correct / len(validation_loader.dataset)
        print(
            f"Epoch {epoch + 1:02d}/{args.epochs} | "
            f"train loss: {loss_sum / len(train_loader.dataset):.4f} | "
            f"validation accuracy: {accuracy:.1%}"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "model_state_dict": {key: value.cpu() for key, value in model.state_dict().items()},
            "input_features": 10,
            "hidden_features": 32,
            "num_classes": 2,
            "seed": args.seed,
            "epochs": args.epochs,
            "validation_accuracy": accuracy,
        },
        args.output,
    )
    print(f"Saved checkpoint to {args.output}")


if __name__ == "__main__":
    main()
