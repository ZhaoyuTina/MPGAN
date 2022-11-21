# MPGAN and GAPT

[![pre-commit.ci status](https://results.pre-commit.ci/badge/github/rkansal47/MPGAN/main.svg)](https://results.pre-commit.ci/latest/github/rkansal47/MPGAN/main)
[![Codestyle](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![DOI](https://zenodo.org/badge/382939833.svg)](https://zenodo.org/badge/latestdoi/382939833)

Code for models introduced in: \
[1] Kansal et. al., *Particle Cloud Generation with Message Passing Generative Adversarial Networks*, NeurIPS 2021 [`arXiv:2106.11535`](https://arxiv.org/abs/2106.11535), and \
[2] Kansal et. al., *On the Evaluation of Generative Models in High Energy Physics*, [`arXiv:2211.10295`](https://arxiv.org/abs/2211.10295)


## Overview

This repository contains PyTorch code for the message-passing GAN ([MPGAN](mpgan/model.py)) and generative adversarial particle transformer ([GAPT]((gapt/model.py))) models, as well as scripts for [training](train.py) the models from scratch, [generating](gen.py) and [plotting](plotting.py) the particle clouds. 
We include also [weights](trained_models) of fully trained models discussed in [1]. 

Additionally, we release the standalone [JetNet](https://github.com/jet-net/JetNet) library, which provides a PyTorch Dataset class for our JetNet dataset, implementations of the evaluation metrics discussed in the paper, and some more useful utilities for development in machine learning + jets.

*For the exact code and scripts used for [1], please see the [neurips21](https://github.com/rkansal47/MPGAN/tree/neurips21) branch.*

## Dependencies

#### MPGAN and GAPT Models

 - `torch >= 1.8.0`

#### Training, Plotting, Evaluation

 - `torch >= 1.8.0`
 - `jetnet >= 0.2.1`
 - `numpy >= 1.21.0`
 - `matplotlib`
 - `mplhep`

 Can be installed via `pip install -r requirements.txt`.

#### External models require

 - `torch`
 - `torch_geometric`


A Docker image containing all necessary libraries can be found [here](https://gitlab-registry.nautilus.optiputer.net/raghsthebest/mnist-graph-gan:nov22) ([Dockerfile](Dockerfile)).


## Training

Start training with:

```python
python train.py --name test_model --model [model] --jets [jets] [args]  
```

where `model` can be specified as `mpgan` or `gapt`, and jets can be any out of `['g', 't', 'q', 'w', 'z']`.

By default, model parameters, figures of particle and jet features, and plots of the training losses and evaluation metrics over time will be saved every five epochs in an automatically created `outputs/[name]` directory.

Some notes:
 - Will run on a GPU by default if available. 
 - The default arguments correspond to the final model architecture and training configuration used in the paper. 
 - Run `python train.py --help` or look at [setup_training.py](setup_training.py) for a full list of arguments.


## Generation

Pre-trained generators with saved state dictionaries and arguments can be used to generate samples with, for example:

```python
python gen.py --G-state-dict trained_models/mp_g/G_best_epoch.pt --G-args trained_models/mp_g/args.txt --num-samples 50,000 --output-file trained_models/mp_g/gen_jets.npy
```
