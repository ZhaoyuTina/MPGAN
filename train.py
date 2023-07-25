import jetnet
from jetnet.datasets import JetNet
from calogan_dataset import CaloGANDataset
from jetnet.datasets.normalisations import FeaturewiseLinearBounded, FeaturewiseLinear


from jetnet import evaluation
import setup_training
from mpgan import augment, mask_manual
import plotting

import torch
from torch import Tensor
from torch.utils.data import DataLoader
from torch.autograd import Variable
from torch.autograd import grad as torch_grad
from torch.distributions.normal import Normal

from collections import Counter

import numpy as np

from os import remove
from os.path import exists

from tqdm import tqdm

import logging


# from guppy import hpy

# h = hpy()

# TODO switch eta phi
LAYER_SPECS = [(3, 96), (12, 12), (12, 6)]
num_layers = len(LAYER_SPECS)
shift = -0.5  # from normalisation
eta_idx = 0
phi_idx = 1
z_idx = 2

# edges for binning z values
# eta and phi are also mapped from layer_specs with pattern, can be represented by range
# eta and phi boundaries should be 2 dimentional, depends on z value


eta_boundaries = [
    (torch.range(1, LAYER_SPECS[i][1] - 1) / LAYER_SPECS[i][1]) + shift for i in range(3)
]

phi_boundaries = [
    (torch.range(1, LAYER_SPECS[i][0] - 1) / LAYER_SPECS[i][0]) + shift for i in range(3)
]

# TODO don't know why should be num_layers - 1, this will have only 1 and 2, is it because mix of range and arange?
# and why shift is -0.5
# Raghav version
z_boundaries = (torch.range(1, num_layers - 1) / num_layers) + shift

# Tina version
# z_boundaries = (torch.range(-1, num_layers -1) / num_layers) - shift


def main():
    device = "cuda" if torch.cuda.is_available() else "cpu"
    torch.autograd.set_detect_anomaly(False)

    args = setup_training.init()
    torch.manual_seed(args.seed)
    args.device = device
    logging.info("Args initalized")


    # particle_norm = FeaturewiseLinearBounded(
    #     feature_norms=1.0,
    #     feature_shifts=[0.0, 0.0, -0.5, -0.5] if args.mask else [0.0, 0.0, -0.5],
    #     feature_maxes=feature_maxes,
    # )

    print("logE, ", args.logE)
    X_train = CaloGANDataset(
        train=True,
        data_dir=args.datasets_path,
        num_particles=args.num_hits,
        logE=args.logE,
        use_mask=args.mask,
        train_fraction=args.ttsplit,
    )
    # jet_norm = FeaturewiseLinear(feature_scales=1.0 / args.num_hits)

    # data_args = {
    #     "jet_type": args.jets,
    #     "data_dir": args.datasets_path,
    #     "num_particles": args.num_hits,
    #     "particle_features": JetNet.all_particle_features
    #     if args.mask
    #     else JetNet.all_particle_features[:-1],
    #     "jet_features": "num_particles"
    #     if (args.clabels or args.mask_c or args.gapt_mask)
    #     else None,
    #     "particle_normalisation": particle_norm,
    #     "jet_normalisation": jet_norm,
    #     "split_fraction": [args.ttsplit, 1 - args.ttsplit, 0],
    # }

    #X_train = JetNet(**data_args, split="train")
    X_train_loaded = DataLoader(X_train, shuffle=True, batch_size=args.batch_size, pin_memory=True)

    X_test = CaloGANDataset(
        train=False,
        data_dir=args.datasets_path,
        num_particles=args.num_hits,
        logE=args.logE,
        use_mask=args.mask,
        train_fraction=args.ttsplit,
    )
    X_test_loaded = DataLoader(X_test, batch_size=args.batch_size, pin_memory=True)
    logging.info(f"Data loaded \n X_train \n {X_train} \n X_test \n {X_test}")

    # print(torch.max(X_train.view(-1, 30 * 4), axis=0))
    # print(torch.max(X_test.view(-1, 30 * 4), axis=0))

    G, D = setup_training.models(args)
    model_train_args, model_eval_args, extra_args = setup_training.get_model_args(args)
    logging.info("Models loaded")

    G_optimizer, D_optimizer = setup_training.optimizers(args, G, D)
    logging.info("Optimizers loaded")

    losses, best_epoch = setup_training.losses(args)

    train(
        args,
        X_train,
        X_train_loaded,
        X_test,
        X_test_loaded,
        G,
        D,
        G_optimizer,
        D_optimizer,
        losses,
        best_epoch,
        model_train_args,
        model_eval_args,
        extra_args,
    )


def get_gen_noise(
    model_args,
    num_samples: int,
    num_particles: int,
    model: str = "mpgan",
    device: str = None,
    noise_std: float = 0.2,
):
    """Gets noise needed for generator, arguments are defined in ``gen`` function below"""

    if device is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"

    dist = Normal(torch.tensor(0.0).to(device), torch.tensor(noise_std).to(device))
    point_noise = None

    if model == "mpgan" or model == "old_mpgan":
        if model_args["lfc"]:
            noise = dist.sample((num_samples, model_args["lfc_latent_size"]))
        else:
            extra_noise_p = int("mask_learn_sep" in model_args and model_args["mask_learn_sep"])
            noise = dist.sample(
                (
                    num_samples,
                    num_particles + extra_noise_p,
                    model_args["latent_node_size"],
                )
            )
    elif model == "gapt":
        noise = dist.sample((num_samples, num_particles, model_args["embed_dim"]))
    elif model == "rgan" or model == "graphcnngan":
        noise = dist.sample((num_samples, model_args["latent_dim"]))
    elif model == "treegan":
        noise = [dist.sample((num_samples, 1, model_args["treegang_features"][0]))]
    elif model == "pcgan":
        noise = dist.sample((num_samples, model_args["pcgan_latent_dim"]))
        if model_args["sample_points"]:
            point_noise = Normal(torch.tensor(0.0).to(device), torch.tensor(1.0).to(device)).sample(
                [num_samples, num_particles, model_args["pcgan_z2_dim"]]
            )

    return noise, point_noise


# Maybe not necessary? Can use the forward directly in Bucketize?
class BucketizeFunction(torch.autograd.Function):
    @staticmethod
    def forward(ctx, input):
        # set values to bin centers, taking care of normalisatio

      
        z_bins = torch.bucketize(input[:, :, z_idx], z_boundaries.to(input.device))
        input[:, :, z_idx] = ((z_bins + 0.5) / num_layers) + shift
        
        # lambda function to map eta and phi to different z
        for i in range(num_layers):
            # need to check whether z_b corresponds to current z values
            filter = z_bins == i
           

            filter_layer = input[filter]
           
            phi_bins = torch.bucketize(
                filter_layer[:, phi_idx], phi_boundaries[i].to(input.device)
            )
            filter_layer[:, phi_idx] = ((phi_bins + 0.5) / LAYER_SPECS[i][0]) + shift
          

          
            eta_bins = torch.bucketize(
                filter_layer[:, eta_idx], eta_boundaries[i].to(input.device)
            )
            
            filter_layer[:, eta_idx] = ((eta_bins + 0.5) / LAYER_SPECS[i][1]) + shift
            input[filter] = filter_layer
        input = torch.round(input, decimals=4)   
        return input

    @staticmethod
    def backward(ctx, grad_output):
        return torch.nn.functional.hardtanh(grad_output)


class BucketizeSTE(torch.nn.Module):
    def __init__(self, device):
        self.device = device
        super(BucketizeSTE, self).__init__()

    def forward(self, x):
        x = BucketizeFunction.apply(x)
        return x


def gen(
    model_args: dict,
    G: torch.nn.Module,
    num_samples: int,
    num_particles: int,
    model: str = "mpgan",
    noise: Tensor = None,
    labels: Tensor = None,
    noise_std: float = 0.2,
    **extra_args,
) -> Tensor:
    """
    Generates ``num_samples`` jets in one go. Can optionally pass pre-specified ``noise``,
    else will randomly sample from a normal distribution.

    Needs an dict ``model_args`` containing the following model-specific args.

    mpgan:
    ``lfc`` (bool) use the latent fully connected layer (/ best football team in the world),
    ``lfc_latent_size`` (int) size of latent layer if ``lfc``,
    ``mask_learn_sep`` (bool) separate layer to learn masks,
    ``latent_node_size`` (int) size of each node's latent space, if not using lfc.

    rgan, graphcnngan:
    ``latent_dim`` (int)

    treegan:
    ``treegang_features`` (list)

    pcgan:
    ``pcgan_latent_dim`` (int),
    ``pcgan_z2_dim`` (int),
    ``sample_points`` (bool),
    ``G_pc`` (torch.nn.Module) if ``sample_points``


    Args:
        model_args: see above.
        G (torch.nn.Module): generator module.
        num_samples (int): # jets to generate.
        num_particles (int): # particles per jet.
        model (str): Choices listed in description. Defaults to "mpgan".
        noise (Tensor): Can optionally pass in your own noise. Defaults to None.
        labels (Tensor): Tensor of labels to condition on. Defaults to None.
        noise_std (float): Standard deviation of the Gaussian noise. Defaults to 0.2.
        **extra_args (type): extra args for generation

    Returns:
        Tensor: generated tensor of shape [num_samples, num_particles, num_features].

    """
    device = next(G.parameters()).device

    if labels is not None:
        assert labels.shape[0] == num_samples, "number of labels doesn't match num_samples"
        labels = labels.to(device)

    if noise is None:
        noise, point_noise = get_gen_noise(
            model_args, num_samples, num_particles, model, device, noise_std
        )

   
    global_noise = torch.randn(num_samples, model_args['global_noise_dim']).to(device) if G.noise_conditioning else None
    semi_gen_data = G(noise, labels, global_noise)

    ste = BucketizeSTE(device)
    gen_data = ste(semi_gen_data)
    
   
    if "mask_manual" in extra_args and extra_args["mask_manual"]:
        # TODO: add pt_cutoff to extra_args
        gen_data = mask_manual(model_args, gen_data, extra_args["pt_cutoff"])

    if model == "pcgan" and model_args["sample_points"]:
        gen_data = model_args["G_pc"](gen_data.unsqueeze(1), point_noise)

    logging.debug(gen_data[0, :10])
    return gen_data


def optional_tqdm(iter_obj, use_tqdm, total=None, desc=None):
    if use_tqdm:
        return tqdm(iter_obj, total=total, desc=desc)
    else:
        return iter_obj


def gen_multi_batch(
    model_args,
    G: torch.nn.Module,
    batch_size: int,
    num_samples: int,
    num_particles: int,
    out_device: str = "cpu",
    detach: bool = False,
    use_tqdm: bool = True,
    model: str = "mpgan",
    noise: Tensor = None,
    labels: Tensor = None,
    noise_std: float = 0.2,
    **extra_args,
) -> Tensor:
    """
    Generates ``num_samples`` jets in batches of ``batch_size``.
    Args are defined in ``gen`` function
    """
    assert out_device == "cuda" or out_device == "cpu", "Invalid device type"

    if labels is not None:
        logging.info(f"labels.shape[0], {labels.shape[0]}")
        logging.info(f"num_samples, {num_samples}")
        print("labels.shape[0]", labels.shape[0])
        print("num_samples", num_samples)
        assert labels.shape[0] == num_samples, "number of labels doesn't match num_samples"
        labels = Tensor(labels)

    gen_data = None

    for i in optional_tqdm(
        range((num_samples // batch_size) + 1), use_tqdm, desc="Generating jets"
    ):
        num_samples_in_batch = min(batch_size, num_samples - (i * batch_size))

        if num_samples_in_batch > 0:
            gen_temp = gen(
                model_args,
                G,
                num_samples=num_samples_in_batch,
                num_particles=num_particles,
                model=model,
                noise=noise,
                labels=None
                if labels is None
                else labels[(i * batch_size) : (i * batch_size) + num_samples_in_batch],
                noise_std=noise_std,
                **extra_args,
            )

            if detach:
                gen_temp = gen_temp.detach()

            gen_temp = gen_temp.to(out_device)

        gen_data = gen_temp if i == 0 else torch.cat((gen_data, gen_temp), axis=0)

    return gen_data


# from https://github.com/EmilienDupont/wgan-gp
def gradient_penalty(gp_lambda, D, real_data, generated_data, batch_size, device, model="mpgan"):
    # Calculate interpolation
    alpha = (
        torch.rand(batch_size, 1, 1).to(device)
        if not model == "pcgan"
        else torch.rand(batch_size, 1).to(device)
    )
    alpha = alpha.expand_as(real_data)
    interpolated = alpha * real_data + (1 - alpha) * generated_data
    interpolated = Variable(interpolated, requires_grad=True).to(device)

    del alpha
    torch.cuda.empty_cache()

    # Calculate probability of interpolated examples
    prob_interpolated = D(interpolated)

    # Calculate gradients of probabilities with respect to examples
    gradients = torch_grad(
        outputs=prob_interpolated,
        inputs=interpolated,
        grad_outputs=torch.ones(prob_interpolated.size()).to(device),
        create_graph=True,
        retain_graph=True,
        allow_unused=True,
    )[0].to(device)
    gradients = gradients.contiguous()

    # Gradients have shape (batch_size, num_channels, img_width, img_height),
    # so flatten to easily take norm per example in batch
    gradients = gradients.view(batch_size, -1)

    # Derivatives of the gradient close to 0 can cause problems because of
    # the square root, so manually calculate norm and add epsilon
    gradients_norm = torch.sqrt(torch.sum(gradients**2, dim=1) + 1e-12)

    # Return gradient penalty
    gp = gp_lambda * ((gradients_norm - 1) ** 2).mean()
    return gp


bce = torch.nn.BCELoss()
mse = torch.nn.MSELoss()


def calc_D_loss(
    loss,
    D,
    data,
    gen_data,
    real_outputs,
    fake_outputs,
    run_batch_size,
    model="mpgan",
    gp_lambda=0,
    label_smoothing=False,
    label_noise=False,
):
    """
    calculates discriminator loss for the different possible loss functions
    + optionally applying label smoothing, label flipping, or a gradient penalty

    returns individual loss contributions as well for evaluation and plotting
    """
    device = data.device

    if loss == "og" or loss == "ls":
        if label_smoothing:
            Y_real = torch.empty(run_batch_size).uniform_(0.7, 1.2).to(device)
            Y_fake = torch.empty(run_batch_size).uniform_(0.0, 0.3).to(device)
        else:
            Y_real = torch.ones(run_batch_size, 1).to(device)
            Y_fake = torch.zeros(run_batch_size, 1).to(device)

        # randomly flipping labels for D
        if label_noise:
            Y_real[torch.rand(run_batch_size) < label_noise] = 0
            Y_fake[torch.rand(run_batch_size) < label_noise] = 1

    if loss == "og":
        D_real_loss = bce(real_outputs, Y_real)
        D_fake_loss = bce(fake_outputs, Y_fake)
    elif loss == "ls":
        D_real_loss = mse(real_outputs, Y_real)
        D_fake_loss = mse(fake_outputs, Y_fake)
    elif loss == "w":
        D_real_loss = -real_outputs.mean()
        D_fake_loss = fake_outputs.mean()
    elif loss == "hinge":
        D_real_loss = torch.nn.ReLU()(1.0 - real_outputs).mean()
        D_fake_loss = torch.nn.ReLU()(1.0 + fake_outputs).mean()

    D_loss = D_real_loss + D_fake_loss

    if gp_lambda:
        gp = gradient_penalty(gp_lambda, D, data, gen_data, run_batch_size, device, model)
        gpitem = gp.item()
        D_loss += gp
    else:
        gpitem = None

    return (
        D_loss,
        {
            "Dr": D_real_loss.item(),
            "Df": D_fake_loss.item(),
            "gp": gpitem,
            "D": D_real_loss.item() + D_fake_loss.item(),
        },
    )


def train_D(
    model_args,
    D,
    G,
    D_optimizer,
    G_optimizer,
    data,
    loss,
    loss_args={},
    gen_args={},
    augment_args=None,
    gen_data=None,
    labels=None,
    model="mpgan",
    epoch=0,
    print_output=False,
    **extra_args,
):
    logging.debug("Training D")
    log = logging.info if print_output else logging.debug

    D.train()
    D_optimizer.zero_grad()
    G.eval()

    run_batch_size = data.shape[0]

    D_real_output = D(data.clone(), labels)
    log(f"D real output: \n {D_real_output[:10]}")

    if gen_data is None:
        gen_data = gen(
            model_args,
            G,
            num_samples=run_batch_size,
            model=model,
            labels=labels,
            **gen_args,
            **extra_args,
        )

    if augment_args is not None and augment_args.augment:
        p = augment_args.aug_prob if not augment_args.adaptive_prob else augment_args.augment_p[-1]
        data = augment.augment(augment_args, data, p)
        gen_data = augment.augment(augment_args, gen_data, p)

    # log(f"G output: \n {gen_data[:2, :10]}")

    D_fake_output = D(gen_data, labels)
    # log(f"D fake output: \n {D_fake_output[:10]}")

    D_loss, D_loss_items = calc_D_loss(
        loss,
        D,
        data,
        gen_data,
        D_real_output,
        D_fake_output,
        run_batch_size,
        model=model,
        **loss_args,
    )
    D_loss.backward()
    D_optimizer.step()
    return D_loss_items


def calc_G_loss(loss, fake_outputs):
    """Calculates generator loss for the different possible loss functions"""
    Y_real = torch.ones(fake_outputs.shape[0], 1, device=fake_outputs.device)

    if loss == "og":
        G_loss = bce(fake_outputs, Y_real)
    elif loss == "ls":
        G_loss = mse(fake_outputs, Y_real)
    elif loss == "w" or loss == "hinge":
        G_loss = -fake_outputs.mean()

    return G_loss


def train_G(
    model_args,
    D,
    G,
    G_optimizer,
    loss,
    batch_size,
    gen_args={},
    augment_args=None,
    labels=None,
    model="mpgan",
    epoch=0,
    **extra_args,
):
    logging.debug("gtrain")
    G.train()
    G_optimizer.zero_grad()

    run_batch_size = labels.shape[0] if labels is not None else batch_size

    gen_data = gen(
        model_args,
        G,
        num_samples=run_batch_size,
        model=model,
        labels=labels,
        **gen_args,
        **extra_args,
    )

    if augment_args is not None and augment_args.augment:
        p = augment_args.aug_prob if not augment_args.adaptive_prob else augment_args.augment_p[-1]
        gen_data = augment.augment(augment_args, gen_data, p)

    D_fake_output = D(gen_data, labels)

    logging.debug("D fake output:")
    logging.debug(D_fake_output[:10])

    G_loss = calc_G_loss(loss, D_fake_output)

    G_loss.backward()
    G_optimizer.step()

    return G_loss.item()


def save_models(D, G, D_optimizer, G_optimizer, models_path, epoch, multi_gpu=False):
    if multi_gpu:
        torch.save(D.module.state_dict(), models_path + "/D_" + str(epoch) + ".pt")
        torch.save(G.module.state_dict(), models_path + "/G_" + str(epoch) + ".pt")
    else:
        torch.save(D.state_dict(), models_path + "/D_" + str(epoch) + ".pt")
        torch.save(G.state_dict(), models_path + "/G_" + str(epoch) + ".pt")

    torch.save(D_optimizer.state_dict(), models_path + "/D_optim_" + str(epoch) + ".pt")
    torch.save(G_optimizer.state_dict(), models_path + "/G_optim_" + str(epoch) + ".pt")


def save_losses(losses, losses_path):
    for key in losses:
        np.savetxt(f"{losses_path}/{key}.txt", losses[key])


def evaluate(
    losses,
    real_jets,
    gen_jets,
    jet_type,
    num_particles=30,
    num_w1_eval_samples=10000,
    num_cov_mmd_eval_samples=100,
    num_fpnd_eval_samples=50000,
    fpnd_batch_size=16,
    efp_jobs=None,
    real_efps=None,
    gen_efps=None,
):
    """Calculate evaluation metrics using the JetNet library and add them to the losses dict"""

    print(real_jets.shape)
    if "w1p" in losses:
        w1pm, w1pstd = evaluation.w1p(
            real_jets,
            gen_jets,
            exclude_zeros=True,
            num_eval_samples=num_w1_eval_samples,
            num_batches=real_jets.shape[0] // num_w1_eval_samples,
            average_over_features=False,
            return_std=True,
        )
        losses["w1p"].append(np.concatenate((w1pm, w1pstd)))

    if "w1m" in losses:
        w1mm, w1mstd = evaluation.w1m(
            real_jets,
            gen_jets,
            num_eval_samples=num_w1_eval_samples,
            num_batches=real_jets.shape[0] // num_w1_eval_samples,
            return_std=True,
        )
        losses["w1m"].append(np.array([w1mm, w1mstd]))

    if "w1efp" in losses:
        w1efpm, w1efpstd = evaluation.w1efp(
            real_jets,
            gen_jets,
            use_particle_masses=False,
            num_eval_samples=num_w1_eval_samples,
            num_batches=real_jets.shape[0] // num_w1_eval_samples,
            average_over_efps=False,
            return_std=True,
            efp_jobs=efp_jobs,
        )
        losses["w1efp"].append(np.concatenate((w1efpm, w1efpstd)))

    if "fpnd" in losses:
        losses["fpnd"].append(
            evaluation.fpnd(
                gen_jets[:num_fpnd_eval_samples, :num_particles],
                jet_type,
                batch_size=fpnd_batch_size,
            )
        )

    # coming soon
    # if "fpd" in losses:
    #     losses["fpd"].append(evaluation.fpd(real_efps, gen_efps, n_jobs=efp_jobs))


def make_plots(
    losses,
    epoch,
    real_jets,
    gen_jets,
    real_mask,
    gen_mask,
    jet_type,
    num_particles,
    name,
    figs_path,
    losses_path,
    save_epochs=5,
    const_ylim=False,
    coords="polarrel",
    dataset="jetnet",
    loss="ls",
    shower_ims=True,
):
    """Plot histograms, jet images, loss curves, and evaluation curves"""

    plotting.plot_hit_feats(
        real_jets,
        gen_jets,
        real_mask,
        gen_mask,
        name=name + "h",
        figs_path=figs_path,
        num_particles=num_particles,
        show=False,
    )

    plotting.plot_layerwise_hit_feats(
        real_jets,
        gen_jets,
        real_mask,
        gen_mask,
        name=name + "lh",
        figs_path=figs_path,
        num_particles=num_particles,
        show=False,
    )

    if shower_ims:
        plotting.plot_shower_ims(
            gen_jets,
            name=name + "_ims",
            figs_path=figs_path,
            show=False,
        )

    if len(losses["G"]) > 1:
        plotting.plot_losses(losses, loss=loss, name=name, losses_path=losses_path, show=False)

        try:
            remove(losses_path + "/" + str(epoch - save_epochs) + ".pdf")
        except:
            logging.info("Couldn't remove previous loss curves")

    # if len(losses["w1p"]) > 1:
    #     plotting.plot_eval(
    #         losses,
    #         epoch,
    #         save_epochs,
    #         coords=coords,
    #         name=name + "_eval",
    #         losses_path=losses_path,
    #         show=False,
    #     )
    #
    #     try:
    #         remove(losses_path + "/" + str(epoch - save_epochs) + "_eval.pdf")
    #     except:
    #         logging.info("Couldn't remove previous eval curves")


def eval_save_plot(
    args,
    X_test,
    D,
    G,
    D_optimizer,
    G_optimizer,
    model_args,
    losses,
    epoch,
    best_epoch,
    **extra_args,
):
    real_jets, real_mask = X_test.unnormalize_features(
        X_test.data[: args.eval_tot_samples].clone(),
        ret_mask_separate=True,
        is_real_data=True,
        zero_mask_particles=True,
        zero_neg_pt=True,
    )
    gen_output = gen_multi_batch(
        model_args,
        G,
        args.batch_size,  
        args.eval_tot_samples, #here
        args.num_hits,
        out_device="cpu",
        model=args.model,
        detach=True,
        labels=X_test.jet_features[: args.eval_tot_samples]  #here
        if (args.mask_c or args.clabels)
        else None,
        **extra_args,
    )
    # TODO Print out number of labels and samples
    # Python debugger breakpoint()
    # comapre what has changed

    gen_jets, gen_mask = X_test.unnormalize_features(
        gen_output,
        ret_mask_separate=True,
        is_real_data=False,
        zero_mask_particles=True,
        zero_neg_pt=True,
    )
    real_jets = real_jets.detach().cpu().numpy()
    if real_mask is not None:
        real_mask = real_mask.detach().cpu().numpy()
    gen_jets = gen_jets.numpy()
    if gen_mask is not None:
        gen_mask = gen_mask.numpy()


    
    save_losses(losses, args.losses_path)

    if args.make_plots:
        make_plots(
            losses,
            epoch,
            real_jets,
            gen_jets,
            real_mask,
            gen_mask,
            args.jets,
            args.num_hits,
            str(epoch),
            args.figs_path,
            args.losses_path,
            save_epochs=args.save_epochs,
            const_ylim=args.const_ylim,
            coords=args.coords,
            loss=args.loss,
            shower_ims=args.shower_ims,
        )

    #  Commented out because deprecated? TODO
    # save model state and sample generated jets if this is the lowest w1m score yet
    # if epoch > 0 and losses["w1m"][-1][0] < best_epoch[-1][1]:
    #     best_epoch.append([epoch, losses["w1m"][-1][0]])
    #     np.savetxt(f"{args.outs_path}/best_epoch.txt", np.array(best_epoch))
    
    #     np.save(f"{args.outs_path}/best_epoch_gen_jets", gen_jets)
    #     np.save(f"{args.outs_path}/best_epoch_gen_mask", gen_mask)
    
    #     with open(f"{args.outs_path}/best_epoch_losses.txt", "w") as f:
    #         f.write(str({key: losses[key][-1] for key in losses}))
    
    #     if args.multi_gpu:
    #         torch.save(G.module.state_dict(), f"{args.outs_path}/G_best_epoch.pt")
    #     else:
    #         torch.save(G.state_dict(), f"{args.outs_path}/G_best_epoch.pt")


def train_loop(
    args,
    X_train_loaded,
    epoch_loss,
    D,
    G,
    D_optimizer,
    G_optimizer,
    gen_args,
    D_losses,
    D_loss_args,
    model_train_args,
    epoch,
    extra_args,
):
    lenX = len(X_train_loaded)
    
    for batch_ndx, data in tqdm(
        enumerate(X_train_loaded), total=lenX, mininterval=0.1, desc=f"Epoch {epoch}"
    ):
        labels = (
            data[1].to(args.device) if (args.clabels or args.mask_c or args.gapt_mask) else None
        )
        data = data[0].to(args.device)

        if args.model == "pcgan":
            # run through pre-trained inference network first i.e. find latent representation
            data = model_train_args["pcgan_G_inv"](data.clone())
        
        
        
        if args.num_critic > 1 or (batch_ndx == 0 or (batch_ndx - 1) % args.num_gen == 0):
            D_loss_items = train_D(
                model_train_args,
                D,
                G,
                D_optimizer,
                G_optimizer,
                data,
                loss=args.loss,
                loss_args=D_loss_args,
                gen_args=gen_args,
                augment_args=args,
                labels=labels,
                model=args.model,
                epoch=epoch - 1,
                # print outputs for the last iteration of each epoch
                print_output=(batch_ndx == lenX - 1),
                **extra_args,
            )

            for key in D_losses:
                epoch_loss[key] += D_loss_items[key]

        if args.num_critic == 1 or (batch_ndx - 1) % args.num_critic == 0:
            epoch_loss["G"] += train_G(
                model_train_args,
                D,
                G,
                G_optimizer,
                loss=args.loss,
                batch_size=args.batch_size,
                gen_args=gen_args,
                augment_args=args,
                labels=labels,
                model=args.model,
                epoch=epoch - 1,
                **extra_args,
            )

        if args.bottleneck:
            if batch_ndx == 10:
                return

        if args.break_zero:
            if batch_ndx == 0:
                break
        

def train(
    args,
    X_train,
    X_train_loaded,
    X_test,
    X_test_loaded,
    G,
    D,
    G_optimizer,
    D_optimizer,
    losses,
    best_epoch,
    model_train_args,
    model_eval_args,
    extra_args,
):
    if args.start_epoch == 0 and args.save_zero:
        logging.info("First eval")
        print("didn't skip")
        
        eval_save_plot(
            args,
            X_test,
            D,
            G,
            D_optimizer,
            G_optimizer,
            model_eval_args,
            losses,
            0,
            best_epoch,
            **extra_args,
        )

    D_losses = ["Dr", "Df", "D"]
    if args.gp:
        D_losses.append("gp")

    epoch_loss = {"G": 0}
    for key in D_losses:
        epoch_loss[key] = 0

    gen_args = {"num_particles": args.num_hits, "noise_std": args.sd}
    D_loss_args = {
        "gp_lambda": args.gp,
        "label_smoothing": args.label_smoothing,
        "label_noise": args.label_noise,
    }
    lenX = len(X_train_loaded)

    for i in range(args.start_epoch, args.num_epochs):
        epoch = i + 1
        logging.info(f"Epoch {epoch} starting")

        for key in epoch_loss:
            epoch_loss[key] = 0

        train_loop(
            args,
            X_train_loaded,
            epoch_loss,
            D,
            G,
            D_optimizer,
            G_optimizer,
            gen_args,
            D_losses,
            D_loss_args,
            model_train_args,
            epoch,
            extra_args,
        )
        logging.info(f"Epoch {epoch} Training Over")

        for key in D_losses:
            losses[key].append(epoch_loss[key] / (lenX / args.num_gen))
        losses["G"].append(epoch_loss["G"] / (lenX / args.num_critic))

        for key in epoch_loss:
            logging.info("{} loss: {:.3f}".format(key, losses[key][-1]))

        if (epoch) % args.save_epochs == 0:
            logging.info("five epochs")
            eval_save_plot(
                args,
                X_test,
                D,
                G,
                D_optimizer,
                G_optimizer,
                model_eval_args,
                losses,
                epoch,
                best_epoch,
                **extra_args,
            )
        elif (epoch) % args.save_model_epochs == 0:
            save_models(
                D, G, D_optimizer, G_optimizer, args.models_path, epoch, multi_gpu=args.multi_gpu
            )


if __name__ == "__main__":
    main()
